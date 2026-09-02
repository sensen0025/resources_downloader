"""Resource Hub API — 客户资源下载接口 v0.2(令牌门槛 + 任务契约 + 传输增强)。

对外契约:
- **令牌门槛**:配置 RH_API_SECRET 后,全部业务端点强制 `Authorization: Bearer <key>`;
  先 POST /api/v1/tokens 申请(每人独立 owner,数据隔离);个人模式(未配置)免认证。
- **任务契约**:统一信封 {code, data} / {code, message, details};
  状态机 queued → running(search/analyze/download/scan/agent) → done|failed|cancelled;
  结果不再回服务器路径,`files[].url` 可下载 + sha256/verdict(查毒)可校验。
- **传输**:轮询(默认)+ SSE 实时事件流 + Webhook 回调(HMAC 签名,指数退避重试);
  文件下载支持 Range 断点续传(206 + Content-Range);download-all 打包 zip。

启动: uvicorn api.app:app --port 8000
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
import zipfile
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .auth import (
    LOCAL_OWNER,
    client_ip,
    current_owner,
    get_token_store,
    is_token_mode,
    is_web_request,
    require_admin,
    resolve_owner,
    web_owner,
)
from .ratelimit import allow as rate_allow
from .settings import env_status, get_masked, update_env
from .tasks_store import TERMINAL_STATUS, TaskStore
from .token_store import TokenStore, new_owner
from .webhooks import deliver_webhook, is_safe_callback_url
from .worker import get_executor

app = FastAPI(
    title="Resource Hub API",
    version="0.2.0",
    description="客户资源下载接口:提交资源任务 → AI 检索/分析/下载/查毒 → 交付文件。"
                "令牌前置(申请 → Bearer 认证 → 轮询/SSE/Webhook + 断点续传下载)。",
)

_store = TaskStore()
_tokens: TokenStore = get_token_store()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data" / "tasks"
WEB_DIR = PROJECT_ROOT / "web"

# 网页控制台静态资源(单页:原生 HTML/JS/CSS,零构建链)
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "dashboard.html")

_STAGE_PERCENT = {"search": 10, "analyze": 30, "download": 60, "novel": 65, "scan": 80, "agent": 70}

# ---------------------------------------------------------------- 统一信封与错误

def _ok(data) -> dict:
    return {"code": 0, "data": data}


def _error(status: int, code: str, message: str, details: Optional[list] = None):
    return JSONResponse(status_code=status,
                        content={"code": code, "message": message, "details": details or []})


_STATUS_CODE_MAP = {
    400: "INVALID_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN",
    404: "NOT_FOUND", 409: "CONFLICT", 422: "INVALID_REQUEST",
    429: "RATE_LIMITED", 500: "INTERNAL_ERROR", 503: "SERVICE_UNAVAILABLE",
}


@app.exception_handler(HTTPException)
async def _http_exc(request: Request, exc: HTTPException):
    code = _STATUS_CODE_MAP.get(exc.status_code, "ERROR")
    return JSONResponse(status_code=exc.status_code,
                        content={"code": code, "message": str(exc.detail), "details": []})


@app.exception_handler(RequestValidationError)
async def _validation_exc(request: Request, exc: RequestValidationError):
    details = [f"{'.'.join(str(p) for p in e.get('loc', []))}: {e.get('msg', '')}"
               for e in exc.errors()[:10]]
    return JSONResponse(status_code=422,
                        content={"code": "INVALID_REQUEST", "message": "参数校验失败",
                                 "details": details})


# ---------------------------------------------------------------- 请求/响应模型

class IntentModel(BaseModel):
    kind: str = ""
    preferred_exts: List[str] = []
    accept_exts: List[str] = []
    constraints: dict = {}


class SkillRequest(BaseModel):
    """定向技能调用(dsh_targeted_skills 白名单:小说/壁纸/游戏/皮肤/图书寻源)。"""

    name: str = Field(max_length=64)
    args: dict = Field(default_factory=dict)


class TaskRequest(BaseModel):
    query: str = Field(default="", max_length=500)       # 防超长 Query 撑爆 Agent/LLM Prompt(ReDoS/上下文溢出)
    seed_urls: List[str] = Field(default_factory=list, max_length=20)
    file_types: List[str] = Field(default_factory=list, max_length=20)
    label: str = Field(default="", max_length=100)
    callback_url: str = Field(default="", max_length=500)   # Webhook:完成/失败时回调
    idempotency_key: str = Field(default="", max_length=100)  # 幂等键(也可放 Idempotency-Key 头)
    login_email: str = Field(default="", max_length=200)   # 需登录站点:注册邮箱(Agent 自动登录/收码)
    username: str = Field(default="", max_length=100)
    use_agent_fallback: bool = True
    security_scan: bool = True          # 下载后查毒闸门(ClamAV+YARA+启发式)
    reuse_cookies: bool = True          # 复用登录态 Cookie
    intent: Optional[IntentModel] = None

    @field_validator("seed_urls")
    @classmethod
    def _seed_urls_len(cls, v: List[str]) -> List[str]:
        for u in v or []:
            if len(u) > 2048:
                raise ValueError("seed_urls 单条 URL 过长(>2048)")
        return v

    @field_validator("file_types")
    @classmethod
    def _file_types_ok(cls, v: List[str]) -> List[str]:
        for e in v or []:
            if len(e) > 12 or not e.startswith("."):
                raise ValueError(f"file_types 项需为 . 开头的短扩展名: {e!r}")
        return v


class TokenApply(BaseModel):
    name: str = Field(default="", max_length=100)
    expires_days: int = Field(default=0, ge=0, le=3650)  # 0 = 不过期


# ---------------------------------------------------------------- 令牌管理

def _resolve_apply_owner(request: Request) -> tuple[str, bool]:
    """申请令牌时的身份:有效 Bearer → 并入其 owner(多设备);否则新建 owner。"""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        tok = _tokens.get_by_key(auth[7:].strip())
        if tok is not None and tok["status"] == "active":
            return tok["owner"], True
    if is_token_mode():
        return new_owner(), False
    return web_owner(request), False  # 个人模式:按 IP 租户(不再共享 local)


@app.post("/api/v1/tokens", status_code=201)
def create_token(body: TokenApply, request: Request) -> dict:
    owner, is_existing = _resolve_apply_owner(request)
    token_id, token_key = _tokens.create(owner, name=body.name,
                                         expires_days=body.expires_days)
    token = _tokens.get(token_id)
    return _ok({
        "token_id": token_id,
        "token_key": token_key,          # ⚠️ 明文仅此一次,请立即保存
        "owner": owner,
        "name": token["name"],
        "created_at": token["created_at"],
        "expires_at": token["expires_at"],
        "multi_device": is_existing,
        "note": "token_key 明文只返回这一次,遗失只能轮换或重新申请",
    })


@app.get("/api/v1/tokens")
def list_tokens(owner: str = Depends(current_owner)) -> dict:
    items = []
    for t in _tokens.list_by_owner(owner):
        items.append({k: t[k] for k in ("id", "name", "scopes", "status",
                                        "created_at", "expires_at", "last_used_at")})
    return _ok({"owner": owner, "count": len(items), "items": items})


@app.post("/api/v1/tokens/{token_id}/rotate")
def rotate_token(token_id: str, owner: str = Depends(current_owner)) -> dict:
    new_id, new_key = _tokens.rotate(token_id, owner) or (None, None)
    if new_id is None:
        return _error(404, "NOT_FOUND", "令牌不存在或已吊销")
    return _ok({"token_id": new_id, "token_key": new_key, "note": "旧令牌已作废,明文仅此一次"})


@app.delete("/api/v1/tokens/{token_id}")
def revoke_token(token_id: str, owner: str = Depends(current_owner)) -> dict:
    if not _tokens.revoke(token_id, owner):
        return _error(404, "NOT_FOUND", "令牌不存在或已吊销")
    return _ok({"revoked": True})


@app.get("/api/v1/me")
def me(owner: str = Depends(current_owner)) -> dict:
    tasks = _store.count(owner)
    storage = 0
    owner_dir = DATA_DIR / owner
    if owner_dir.exists():
        for p in owner_dir.rglob("*"):
            if p.is_file():
                try:
                    storage += p.stat().st_size
                except OSError:
                    pass
    return _ok({
        "owner": owner,
        "token_mode": is_token_mode(),
        "tokens": len(_tokens.list_by_owner(owner)),
        "tasks": tasks,
        "storage_bytes": storage,
    })


# ---------------------------------------------------------------- 任务契约

_SKILL_NAMES = ("biquge_novel_crawler", "haowallpaper_4k_extractor",
                "gdgame_resource_fetcher", "littleskin_texture_extractor",
                "annas_archive_book_finder")


@app.post("/api/v1/skills/run")
def run_skill_api(req: SkillRequest, request: Request,
                  owner: str = Depends(current_owner)) -> dict:
    """定向技能执行(DSH skill_invoke 的后端):白名单 + schema 校验 + 同步执行。

    与任务管线正交 —— 这些是「单站点确定性提取器」,由 AI(DSH agent /
    Resource Hub Agent)决定何时调用;耗时技能(biquge 全本等)建议在任务管线
    内通过 resource_fetch(query) 走异步任务,而非此处长阻塞。
    """
    if req.name not in _SKILL_NAMES:
        return _error(422, "UNKNOWN_SKILL",
                      f"未知技能 {req.name!r},可选: {', '.join(_SKILL_NAMES)}")
    try:
        import dsh_targeted_skills as dts
    except Exception as e:
        return _error(500, "SKILL_IMPORT_FAILED", f"技能模块不可用: {type(e).__name__}: {str(e)[:120]}")
    tr = dts.run_skill(req.name, dict(req.args or {}))
    data = dict(tr.data or {})
    data.pop("_tool", None)
    if tr.ok:
        return _ok({"name": req.name, "ok": True, "message": tr.message, "data": data})
    code = "INVALID_ARGS" if tr.error == "INVALID_ARGS" else (tr.error or "SKILL_FAILED")
    return _error(422 if code == "INVALID_ARGS" else 500, code, tr.message)


@app.post("/api/v1/tasks", status_code=202)
def create_task(req: TaskRequest, request: Request,
                owner: str = Depends(current_owner)) -> dict:
    if not req.query and not req.seed_urls:
        return _error(422, "INVALID_REQUEST", "query 与 seed_urls 至少给一个")
    if req.callback_url and not is_safe_callback_url(req.callback_url):
        return _error(422, "INVALID_REQUEST", "callback_url 仅允许 http/https")
    if req.file_types and not all(isinstance(e, str) and e.startswith(".") for e in req.file_types):
        return _error(422, "INVALID_REQUEST", "file_types 需为 . 开头的扩展名列表")

    key = req.idempotency_key or request.headers.get("idempotency-key", "")
    if key:
        existing = _store.find_by_idempotency(owner, key)
        if existing is not None:
            if existing["status"] in TERMINAL_STATUS:
                return _ok(_task_payload(existing))  # 幂等命中:返回既有结果
            return _error(409, "CONFLICT", f"Idempotency-Key {key!r} 已有进行中的任务")

    file_token = secrets.token_urlsafe(24)
    task_id = _store.create(
        owner, req.query or (req.seed_urls[0] if req.seed_urls else ""),
        label=req.label, request=req.model_dump(), callback_url=req.callback_url,
        idempotency_key=key, file_token=file_token,
    )
    if not get_executor().submit(task_id, lambda: _run_task(task_id, req, owner)):
        _store.set_result(task_id, "failed", {"success": False, "error": "队列已满,请稍后重试"},
                          error="QUEUE_FULL")
        return _error(503, "SERVICE_UNAVAILABLE", "任务队列已满,请稍后重试")

    return _ok({
        "task_id": task_id,
        "status": "queued",
        "polling_url": f"/api/v1/tasks/{task_id}",
        "events_url": f"/api/v1/tasks/{task_id}/events",
        "file_token": file_token,   # 文件下载凭证(防越权)
        "note": "用 polling_url 轮询或 events_url 走 SSE 实时进度",
    })


@app.get("/api/v1/tasks")
def list_tasks(limit: int = 20, offset: int = 0, status: str = "",
               owner: str = Depends(current_owner)) -> dict:
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    valid_status = ("queued", "running", "done", "failed", "cancelled")
    if status and status not in valid_status:
        return _error(422, "INVALID_REQUEST", f"status 需在 {valid_status} 内")
    rows = _store.list(owner, limit=limit, offset=offset, status=status)
    return _ok({"total": _store.count(owner), "limit": limit, "offset": offset,
                "items": rows})


def _get_owned(task_id: str, owner: str) -> dict:
    task = _store.get(task_id)
    if task is None or task["owner"] != owner:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


def _task_payload(task: dict) -> dict:
    p = {k: task.get(k) for k in (
        "label", "query", "status", "stage", "percent", "error",
        "created_at", "started_at", "finished_at", "events", "result", "files")}
    p["task_id"] = task["id"]
    return p


@app.get("/api/v1/tasks/{task_id}")
def get_task(task_id: str, owner: str = Depends(current_owner)) -> dict:
    task = _get_owned(task_id, owner)
    payload = _task_payload(task)
    payload["files"] = [_file_public(f, task_id) for f in (task.get("files") or [])]
    payload["pan_links"] = (task.get("result") or {}).get("pan_links") or []
    return _ok(payload)


@app.post("/api/v1/tasks/{task_id}/cancel")
def cancel_task(task_id: str, owner: str = Depends(current_owner)) -> dict:
    task = _get_owned(task_id, owner)
    if task["status"] in TERMINAL_STATUS:
        return _ok({"task_id": task_id, "status": task["status"], "message": "任务已结束"})
    _store.request_cancel(task_id)
    return _ok({"task_id": task_id, "status": "cancelling",
                "message": "已受理取消,任务将尽快停止(协作式,最长一步内生效)"})


# ---------------------------------------------------------------- SSE 实时事件流

@app.get("/api/v1/tasks/{task_id}/events")
def task_events(task_id: str, owner: str = Depends(current_owner)) -> StreamingResponse:
    _get_owned(task_id, owner)

    def gen():
        last = 0
        while True:
            task = _store.get(task_id)
            if task is None:
                break
            for e in (task.get("events") or [])[last:]:
                # 终态事件不重放:末尾由统一的 event: done/failed 帧承载(含 status),
                # 否则客户端会先收到一条无 status 字段的存库终态帧
                # (网页 log 出现 "undefined: 任务结束: undefined")
                if e["type"] in TERMINAL_STATUS:
                    continue
                yield f"event: {e['type']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n"
            last = len(task.get("events") or [])
            if task["status"] in TERMINAL_STATUS:
                yield ("event: done\ndata: " +
                       json.dumps({"status": task["status"], "task_id": task_id},
                                  ensure_ascii=False) + "\n\n")
                break
            yield ": keep-alive\n\n"
            time.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- 文件交付(断点续传)

def _resolve_file_owner(request: Request, task_id: str) -> str:
    """文件访问授权:file_token(分享链接)/ 同 owner 的 Bearer / 同 owner 的网页或匿名请求。

    移除旧的"个人模式一律放行"逻辑 —— 公网个人模式下任意访客都能拿到
    任意任务文件(原缺陷)。现在必须与任务 owner(按 IP 租户或令牌)一致。
    """
    task = _store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if request.query_params.get("token") == task["file_token"]:
        return task["owner"]
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        tok = _tokens.get_by_key(auth[7:].strip())
        if tok is not None and tok["status"] == "active" and tok["owner"] == task["owner"]:
            return task["owner"]
    try:
        owner = resolve_owner(request, _tokens)
    except HTTPException:
        owner = None
    if owner == task["owner"]:
        return owner
    raise HTTPException(status_code=403, detail="无权访问该任务文件(需文件 token 或本人身份)")


def _safe_resolve(task_dir: Path, name: str) -> Optional[Path]:
    """防路径穿越:只允许任务沙箱内的单层文件名。"""
    if not name or Path(name).name != name:
        return None
    p = (task_dir / name).resolve()
    try:
        if not p.is_relative_to(task_dir.resolve()):
            return None
    except AttributeError:  # Python <3.9
        if str(task_dir.resolve()) not in str(p):
            return None
    return p


def _content_disposition(filename: str) -> str:
    """RFC 5987 附件头:中文文件名用 filename*=UTF-8''<percent-encoded>。

    直接拼原始中文会触发 uvicorn latin-1 头部编码异常(HTTP 500)——
    中文文件名的任务下载按钮一点就 500(线上事故复现)。
    """
    from urllib.parse import quote

    ascii_name = filename.encode("ascii", "replace").decode("ascii").replace('"', "_")
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename)}'


def _serve_range(request: Request, path: Path, filename: str = "",
                 content_type: str = "application/octet-stream") -> Response:
    """Range 断点续传下载(206 + Content-Range + Accept-Ranges)。"""
    size = path.stat().st_size
    filename = filename or path.name
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": _content_disposition(filename),
        "Content-Type": content_type,
    }
    start, end = 0, size - 1
    status = 200
    range_header = request.headers.get("range", "")
    if range_header:
        m = re.match(r"bytes=(\d*)-(\d*)$", range_header.strip())
        if m:
            s, e = m.group(1), m.group(2)
            if s == "" and e != "":          # 后缀区间 bytes=-N
                start = max(0, size - int(e))
            else:
                start = int(s) if s else 0
                end = int(e) if e else size - 1
            end = min(end, size - 1)
            if start > end or start >= size:
                return Response(status_code=416,
                                headers={"Content-Range": f"bytes */{size}"})
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    headers["Content-Length"] = str(end - start + 1)

    def iter_chunks():
        with open(path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(1 << 16, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(iter_chunks(), status_code=status, headers=headers)


@app.get("/api/v1/tasks/{task_id}/files/{name}")
def download_file(task_id: str, name: str, request: Request) -> Response:
    owner = _resolve_file_owner(request, task_id)
    path = _safe_resolve(DATA_DIR / owner / task_id, name)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return _serve_range(request, path)


@app.get("/api/v1/tasks/{task_id}/download-all")
def download_all(task_id: str, request: Request) -> Response:
    owner = _resolve_file_owner(request, task_id)
    task = _store.get(task_id)
    task_dir = DATA_DIR / owner / task_id
    files = sorted(
        p for p in task_dir.iterdir()
        if p.is_file() and not p.name.endswith(".part") and p.name != "bundle.zip"
    ) if task_dir.exists() else []
    if not files:
        raise HTTPException(status_code=404, detail="任务没有可下载的文件")
    bundle = task_dir / "bundle.zip"
    if not (bundle.exists() and all(f.stat().st_mtime <= bundle.stat().st_mtime for f in files)):
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as z:
            for f in files:
                z.write(f, arcname=f.name)
    label = (task or {}).get("label") or task_id
    return _serve_range(request, bundle, filename=f"{label}-files.zip",
                        content_type="application/zip")


# ---------------------------------------------------------------- 健康检查

@app.get("/health")
def health() -> dict:
    return {"ok": True, "token_mode": is_token_mode()}


# ---------------------------------------------------------------- 网页控制台配置

@app.get("/api/v1/status")
def status() -> dict:
    """状态栏摘要(公开:登录页需要它判断是否要输令牌)。"""
    st = env_status()
    try:
        from skills.security import detect_engines

        engines = detect_engines()
        st["clamav"] = engines.get("clamav", False)
        st["yara"] = engines.get("yara", False)
    except Exception:
        st["clamav"] = st["yara"] = False
    return _ok(st)


@app.get("/api/v1/settings")
def get_settings(request: Request) -> dict:
    """配置视图(管理员):脱敏,但只有 RH_ADMIN_TOKEN/回环可见 ——
    公网匿名读配置(原缺陷:泄露运行状态)。"""
    require_admin(request)
    return _ok({"fields": get_masked()})


class SettingsUpdate(BaseModel):
    llm_api_key: Optional[str] = None
    llm_base_url: Optional[str] = None
    llm_model: Optional[str] = None
    mail_imap_host: Optional[str] = None
    mail_imap_port: Optional[str] = None
    mail_email: Optional[str] = None
    mail_password: Optional[str] = None
    proxy_url: Optional[str] = None
    api_secret: Optional[str] = None


@app.put("/api/v1/settings")
def put_settings(body: SettingsUpdate, request: Request) -> dict:
    """保存配置(管理员):立即生效(os.environ)+ 持久化(.env)。

    公网匿名可覆写 = 致命:攻击者可写入 api_secret 锁死控制台 / 改 proxy_url
    劫持流量 / 清空 llm_api_key 瘫痪服务(原缺陷 1)。必须管理员。
    "" = 清除,None = 不改。"""
    require_admin(request)
    changes = update_env(body.model_dump())
    return _ok({"changes": changes, "status": env_status()})


class ScanRequest(BaseModel):
    path: str


def _scan_path_allowed(p: Path) -> bool:
    """扫描路径沙箱:仅允许 downloads/ 与 data/tasks/ 内的路径。

    原缺陷 2:任意 path(如 /etc/passwd、.env)都能被公网探测并回显扫描结果。
    DATA_DIR 在运行时取值(测试会重定向到沙箱,模块级常量会拿到旧值)。
    """
    try:
        rp = p.resolve()
    except Exception:
        return False
    for root in (PROJECT_ROOT / "downloads", DATA_DIR):
        try:
            rr = root.resolve()
            if rp == rr or rp.is_relative_to(rr):
                return True
        except (OSError, AttributeError):
            continue
    return False


@app.post("/api/v1/scan")
def scan_file_endpoint(body: ScanRequest, request: Request) -> dict:
    """查毒技能可视化(管理员 + 路径沙箱):扫描 downloads/ 与 data/tasks/ 内文件。"""
    require_admin(request)
    p = Path(body.path)
    if not _scan_path_allowed(p):
        return _error(403, "FORBIDDEN",
                      "仅允许扫描 downloads/ 与 data/tasks/ 目录内的路径(防任意文件探测)")
    if not p.exists():
        return _error(404, "NOT_FOUND", f"路径不存在: {body.path}")
    from skills.security import scan_file

    r = scan_file(p)
    return _ok(r.to_dict())


class LlmTestResult(BaseModel):
    ok: bool
    latency_ms: int = 0
    error: str = ""


@app.post("/api/v1/test-llm")
def test_llm(request: Request) -> dict:
    """配置页「测试 LLM」(管理员 + IP 频控):每次调用都会真实消耗 LLM 额度,
    公网匿名可刷 = 资金损失(原缺陷 4)。"""
    require_admin(request)
    if not rate_allow(f"test_llm:{client_ip(request)}", limit=5, window=60):
        return _error(429, "RATE_LIMITED", "操作过于频繁,请稍后再试(每分钟限 5 次)")
    import time

    from agent.llm import LLMClient

    t0 = time.monotonic()
    try:
        client = LLMClient(timeout=30)
        reply = client.chat([{"role": "user", "content": "回复 OK 两个字母"}],
                            max_tokens=64)
        latency = int((time.monotonic() - t0) * 1000)
        return _ok({"ok": True, "latency_ms": latency, "reply": str(reply)[:50]})
    except Exception as e:
        latency = int((time.monotonic() - t0) * 1000)
        return _ok({"ok": False, "latency_ms": latency,
                    "error": f"{type(e).__name__}: {str(e)[:200]}"})


# ---------------------------------------------------------------- 任务执行

def _run_task(task_id: str, req: TaskRequest, owner: str) -> None:
    from agent.tasks.fetch_resource import TaskCancelled, fetch_resource

    out_dir = DATA_DIR / owner / task_id
    _store.set_running(task_id)

    def on_stage(stage: str, message: str) -> None:
        _store.set_progress(task_id, stage, _STAGE_PERCENT.get(stage, 50), message)

    def is_cancelled() -> bool:
        return _store.is_cancel_requested(task_id)

    try:
        result = fetch_resource(
            query=req.query,
            seed_urls=req.seed_urls or None,
            file_types=tuple(req.file_types) if req.file_types else None,
            out_dir=out_dir,
            login_email=req.login_email,
            username=req.username,
            use_agent_fallback=req.use_agent_fallback,
            security_scan=req.security_scan,
            reuse_cookies=req.reuse_cookies,
            on_stage=on_stage,
            is_cancelled=is_cancelled,
            verbose=False,
        )
        if is_cancelled():
            status = "cancelled"
            result.error = "cancelled"
            result.summary = "任务已取消"
        else:
            status = "done" if result.success else "failed"
        files = _collect_files(task_id, owner, result.files)
        _store.set_result(task_id, status, result.to_dict(), files, error=result.error)
        if req.callback_url and status in TERMINAL_STATUS:
            deliver_webhook(req.callback_url, {
                "event": f"task.{status}", "task_id": task_id, "status": status,
                "result": result.to_dict(),
                "files": [_file_public(f, task_id) for f in files],
            })
    except TaskCancelled:
        _store.set_result(task_id, "cancelled",
                          {"success": False, "summary": "任务已取消", "error": "cancelled"},
                          error="cancelled")
    except Exception as e:
        _store.set_result(task_id, "failed",
                          {"success": False, "summary": f"{type(e).__name__}: {str(e)[:200]}",
                           "error": str(e)[:300]},
                          error=str(e)[:300])


def _file_public(f: dict, task_id: str) -> dict:
    """文件条目公开形态:不暴露服务器路径,url 是可下载地址。"""
    out = dict(f)
    out["url"] = f"/api/v1/tasks/{task_id}/files/{f.get('name', '')}"
    out.pop("path", None)
    return out


def _collect_files(task_id: str, owner: str, result_files: list) -> list[dict]:
    out_dir = DATA_DIR / owner / task_id
    items = []
    for f in result_files or []:
        p = Path(f)
        try:
            if not p.is_file():
                continue
            items.append({
                "name": p.name,
                "size": p.stat().st_size,
                "sha256": _sha256(p),
                "verdict": _verdict(p),
            })
        except Exception:
            continue
    return items


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _verdict(path: Path) -> str:
    """查毒结果透传(与下载闸门同一套引擎);失败标 unknown 不阻塞。"""
    try:
        from skills.security import scan_file

        return scan_file(path).verdict
    except Exception:
        return "unknown"
