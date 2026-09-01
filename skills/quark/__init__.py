"""夸克网盘自动解析技能 — 分享链接 → 文件清单 → 转存 → 直链下载,全自动。

    from skills.quark import list_share, download_share, auth_status, auth_import

一句话:给一个 pan.quark.cn/s/xxx 分享链接(可带密码),本技能自动:
1. 匿名拿 stoken → 递归列出分享内所有文件(含子目录);
2. 按意图(扩展名/文件名过滤/最大文件)选目标;
3. 转存到自己的网盘 → 轮询任务 → 换直链;
4. 带夸克 UA/Cookie/Referer 下载落地,下载后清理转存残留。

登录态:只需一次性导入 Cookie(env QUARK_COOKIE 或 accounts/quark_cookie.json),
之后全自动 —— 不需要用户每次扫码。没有 Cookie 时仍可浏览分享文件清单。

    python -m skills.quark auth status
    python -m skills.quark auth import "__puus=...; __pus=..."
    python -m skills.quark list "https://pan.quark.cn/s/xxxx"
    python -m skills.quark download "https://pan.quark.cn/s/xxxx" --filter 投影 --out downloads
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Callable, Optional

from .api import (
    BASE_HOST,
    QuarkAuthError,
    QuarkClient,
    QuarkError,
    cookie_to_dict,
)

__all__ = [
    "parse_share_url",
    "list_share",
    "download_share",
    "pick_share_file",
    "auth_status",
    "auth_import",
    "cookie_path",
    "load_cookie",
    "QuarkClient",
    "QuarkError",
    "QuarkAuthError",
]
__version__ = "0.1.0"

_PROJECT_ROOT = Path(__file__).resolve().parents[2]          # resource-hub/
COOKIE_FILE = _PROJECT_ROOT / "accounts" / "quark_cookie.json"
_COOKIE_ENV = "QUARK_COOKIE"

_SHARE_RE = re.compile(r"pan\.quark\.cn/s/([a-zA-Z0-9]+)", re.I)
_PWD_RE = re.compile(r"(?:pwd|password|passcode|提取码|密码)[=：: ]*\s*([a-zA-Z0-9]{2,})", re.I)


def parse_share_url(url: str) -> tuple[str, str]:
    """解析分享链接 → (pwd_id, password)。密码来自 ?pwd= 或『密码/提取码:xxx』。"""
    url = (url or "").strip()
    m = _SHARE_RE.search(url)
    if not m:
        raise QuarkError(f"不是有效的夸克分享链接: {url[:80]}")
    pwd_id = m.group(1)
    pm = _PWD_RE.search(url)
    password = pm.group(1) if pm else ""
    return pwd_id, password


def cookie_path() -> Path:
    return COOKIE_FILE


def load_cookie() -> str:
    """Cookie 来源优先级:env QUARK_COOKIE > accounts/quark_cookie.json。"""
    env = os.environ.get(_COOKIE_ENV, "").strip()
    if env:
        return env
    p = COOKIE_FILE
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            ck = str(data.get("cookie") or "").strip()
            if ck:
                return ck
        except Exception:
            pass
    return ""


def auth_import(cookie: str) -> Path:
    """一次性导入 Cookie 到 accounts/quark_cookie.json。"""
    cookie = (cookie or "").strip()
    if not cookie:
        raise QuarkError("Cookie 为空")
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    COOKIE_FILE.write_text(
        json.dumps({"cookie": cookie, "source": "manual-import"}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return COOKIE_FILE


def auth_status(verbose: bool = False) -> dict:
    """检查登录态:{logged_in, cookie, nickname?}。"""
    ck = load_cookie()
    if not ck:
        return {"logged_in": False, "cookie": "", "reason": "未配置 Cookie(env QUARK_COOKIE 或 accounts/quark_cookie.json)"}
    if "__puus" not in ck:
        return {"logged_in": False, "cookie": ck[:24] + "…" if len(ck) > 24 else ck,
                "reason": "Cookie 缺少 __puus(夸克会话令牌)。请在已登录 pan.quark.cn 的浏览器 "
                          "DevTools → Application → Cookies 里复制 __puus 的值,连同其它 Cookie 一起导入"}
    with QuarkClient(cookie=ck) as client:
        ok = client.is_logged_in()
    return {"logged_in": ok, "cookie": ck[:24] + "…" if len(ck) > 24 else ck,
            "reason": "登录态有效" if ok else "Cookie 失效/被拒,请重新导入"}


def _client(cookie: str = "") -> QuarkClient:
    return QuarkClient(cookie=cookie or load_cookie(),
                       on_cookie_refresh=_persist_cookie)


def _persist_cookie(cookie: str) -> None:
    """响应 Set-Cookie 续期后回写存储(best-effort)。"""
    try:
        if os.environ.get(_COOKIE_ENV):
            return  # env 来源不落盘
        COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
        COOKIE_FILE.write_text(
            json.dumps({"cookie": cookie, "source": "auto-refresh"}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    except Exception:
        pass


# ---------------------------------------------------------------- 浏览

def list_share(url: str, password: str = "", max_depth: int = 3,
               cookie: str = "", on_stage: Optional[Callable[[str, str], None]] = None) -> dict:
    """列分享全部文件(递归)。返回 {pwd_id, files:[{path,file_name,size,fid,dir}...]}。"""
    pwd_id, pwd = parse_share_url(url)
    password = password or pwd
    with _client(cookie) as client:
        stoken = client.get_stoken(pwd_id, password)
        if on_stage:
            on_stage("quark", f"已获取分享令牌,列出文件…")
        items = client.walk_share(pwd_id, stoken, max_depth=max_depth)
    files = []
    for it in items:
        files.append({
            "path": it.get("path", ""),
            "file_name": it.get("file_name", ""),
            "size": int(it.get("size") or 0),
            "fid": it.get("fid", ""),
            "dir": bool(it.get("dir")),
        })
    return {"pwd_id": pwd_id, "files": files}


def pick_share_file(files: list[dict], file_filter: str = "",
                    preferred_exts: tuple[str, ...] = (),
                    accept_exts: tuple[str, ...] = ()) -> Optional[dict]:
    """从分享文件里挑目标:过滤词 > 首选扩展名 > 兜底扩展名 > 最大文件。"""
    cands = [f for f in files if not f.get("dir")]
    if not cands:
        return None
    if file_filter:
        low = file_filter.lower()
        exact = [f for f in cands if f.get("file_name", "").lower() == low]
        if exact:
            return exact[0]
        sub = [f for f in cands if low in f.get("file_name", "").lower()
               or low in f.get("path", "").lower()]
        if sub:
            return max(sub, key=lambda f: f.get("size") or 0)
    pref = [f for f in cands if _ext_match(f.get("file_name", ""), preferred_exts)]
    if pref:
        return max(pref, key=lambda f: f.get("size") or 0)
    acc = [f for f in cands if _ext_match(f.get("file_name", ""), accept_exts)]
    if acc:
        return max(acc, key=lambda f: f.get("size") or 0)
    return max(cands, key=lambda f: f.get("size") or 0)


def _ext_match(name: str, exts: tuple[str, ...]) -> bool:
    low = name.lower()
    return any(low.endswith(e) for e in exts if e)


# ---------------------------------------------------------------- 下载

def download_share(url: str, password: str = "", file_filter: str = "",
                   dest_dir: str | Path = "downloads", *,
                   preferred_exts: tuple[str, ...] = (),
                   accept_exts: tuple[str, ...] = (),
                   cookie: str = "", cleanup: bool = True,
                   on_stage: Optional[Callable[[str, str], None]] = None,
                   progress: Optional[Callable[[int, int], None]] = None) -> dict:
    """分享链接 → 转存 → 直链 → 下载落地。

    返回 {ok, path?, file_name, size, error?, share_url}。
    没有 Cookie 时抛出 QuarkAuthError(需要一次性导入,之后全自动)。
    """
    dest = Path(dest_dir)
    pwd_id, pwd = parse_share_url(url)
    password = password or pwd
    ck = cookie or load_cookie()
    if not ck:
        raise QuarkAuthError(
            "下载需要夸克登录 Cookie(env QUARK_COOKIE 或 python -m skills.quark auth import)。"
            "只需导入一次,之后全自动,无需扫码。"
        )
    with _client(ck) as client:
        if on_stage:
            on_stage("quark", f"解析分享 {pwd_id}…")
        stoken = client.get_stoken(pwd_id, password)
        if on_stage:
            on_stage("quark", "获取文件清单…")
        items = client.walk_share(pwd_id, stoken)
        files = [{
            "path": it.get("path", ""),
            "file_name": it.get("file_name", ""),
            "size": int(it.get("size") or 0),
            "fid": it.get("fid", ""),
            "dir": bool(it.get("dir")),
            "share_fid_token": it.get("share_fid_token", ""),
        } for it in items]
        target = pick_share_file(files, file_filter, preferred_exts, accept_exts)
        if target is None:
            raise QuarkError("分享内没有可下载的文件")
        if on_stage:
            on_stage("quark", f"选中: {target['file_name']} ({target.get('size', 0)} B)")
        # 转存:需要 share_fid_token(在父目录的 detail 里)—— walk 已带上
        fid_token = target.get("share_fid_token", "")
        pdir_fid = target.get("pdir_fid", "0")
        if on_stage:
            on_stage("quark", "转存到自己的网盘…")
        task_id = client.save_share(pwd_id, stoken, pdir_fid,
                                    [target["fid"]], [fid_token] if fid_token else [])
        task = client.wait_task(task_id, timeout=90)
        save_as = task.get("save_as") or {}
        saved_fids = save_as.get("save_as_top_fids") or []
        if not saved_fids:
            # 部分响应结构不同:save_as 可能是列表
            raise QuarkError(f"转存完成但未取到保存 fid: {str(save_as)[:120]}")
        saved_fid = saved_fids[0]
        if on_stage:
            on_stage("quark", "换取直链…")
        dl_list = client.get_download_urls([saved_fid])
        if not dl_list or not dl_list[0].get("download_url"):
            raise QuarkError("换取直链失败")
        dl = dl_list[0]
        dl_url = dl["download_url"]
        fname = dl.get("file_name") or target.get("file_name") or "quark_download.bin"
        if on_stage:
            on_stage("quark", f"下载 {fname}…")
        saved = client.download_url(dl_url, dest, filename=fname, progress=progress)
        size = saved.stat().st_size
        if cleanup:
            try:
                client.delete_files([saved_fid])
            except Exception:
                pass  # 清理失败不阻断交付
        return {
            "ok": True,
            "path": str(saved),
            "file_name": fname,
            "size": size,
            "share_url": url,
            "pwd_id": pwd_id,
        }
