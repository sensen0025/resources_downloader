"""资源获取任务 — 检索 → 页面分析 → 快路径直链下载 / 慢路径浏览器 Agent。

对齐计划 v4 §1 端到端流程与 §3.4 两层获取:
- 快路径:候选直链直接下载(零浏览器);
- 慢路径:详情页带下载按钮 / 需登录 / 反爬拦截 的候选 → AccountAgent(fetch_resource goal)
  打开页面,调技能(mail_code/captcha/human/analyze_page/download)取资源;
- 完成判定 = 下载探针(文件落地 + 扩展名 + 非空),模型判定不算数。

用法:
    from agent.tasks.fetch_resource import fetch_resource
    result = fetch_resource("故宫 投影 litematic", out_dir="downloads")
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path
from typing import Callable, Optional

from search import search as search_engines
from pages import analyze_page
from pages.models import PageClass
from agent.tasks.base import FileProbe, TaskResult

DEFAULT_FILE_TYPES = (".litematic", ".schematic", ".schem", ".zip")
DEFAULT_OUT = "downloads"


class TaskCancelled(Exception):
    """协作式取消:任务循环内检查到取消标志时抛出,终止整个任务。"""


def _emit(on_stage: Optional[Callable[[str, str], None]], stage: str, msg: str) -> None:
    if on_stage is not None:
        try:
            on_stage(stage, msg)
        except Exception:
            pass


def _check_cancel(is_cancelled: Optional[Callable[[], bool]]) -> None:
    if is_cancelled is not None:
        try:
            if is_cancelled():
                raise TaskCancelled()
        except TaskCancelled:
            raise
        except Exception:
            pass

# 资源类型预设:按查询关键词自动推断(file_types=None 时)
_TYPE_PRESETS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "schematic": ((".litematic", ".schematic", ".schem", ".zip", ".mcworld"),
                  ("投影", "schematic", "litematic", "建筑", "蓝图", "地图")),
    "image": ((".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"),
              ("壁纸", "图片", "图像", "高清", "wallpaper", "4k", "png", "jpg", "photo", "image")),
    "media": ((".mp4", ".mkv", ".flv", ".mp3", ".flac", ".zip"),
              ("视频", "电影", "电视剧", "动漫", "剧集", "mp4", "video", "music", "音乐")),
    "document": ((".pdf", ".epub", ".mobi", ".azw3", ".djvu", ".txt", ".docx"),
                 ("pdf", "epub", "mobi", "书", "教程", "文档", "资料", "电子书", "doc")),
}

_AGENT_CLASSES = (PageClass.LOGIN_REQUIRED, PageClass.UNKNOWN,
                  PageClass.AGGREGATOR, PageClass.BLOCKED)
_MAX_ANALYZE = 10          # 分析预算
_MAX_AGENT_ATTEMPTS = 2    # Agent 尝试上限


def _infer_file_types(query: str, file_types) -> tuple[str, ...]:
    """按查询关键词推断资源类型;未命中返回全文档预设。"""
    if file_types is not None:
        return tuple(file_types)
    q = (query or "").lower()
    best: tuple[tuple[str, ...], tuple[str, ...]] | None = None
    for preset, (exts, kws) in _TYPE_PRESETS.items():
        if any(k in q for k in kws):
            if best is None or len(kws) > len(best[1]):
                best = (exts, kws)
    if best:
        return best[0]
    return (".pdf", ".epub", ".mobi", ".zip", ".litematic", ".schematic",
            ".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mkv")

# 需要浏览器 Agent 的页面类型(交互/登录/反爬)
_AGENT_CLASSES = (PageClass.LOGIN_REQUIRED, PageClass.UNKNOWN,
                  PageClass.AGGREGATOR, PageClass.BLOCKED)
_MAX_ANALYZE = 10          # 分析预算
_MAX_AGENT_ATTEMPTS = 2    # Agent 尝试上限


def fetch_resource(
    query: str = "",
    seed_urls: Optional[list[str]] = None,
    file_types: Optional[tuple[str, ...]] = None,
    out_dir: str | Path = DEFAULT_OUT,
    engines: Optional[list[str]] = None,
    max_candidates: int = 8,
    use_agent_fallback: bool = True,
    login_email: str = "",
    username: str = "",
    security_scan: bool = True,   # 下载后查毒闸门(ClamAV + YARA + 启发式)
    reuse_cookies: bool = True,   # 复用 accounts/cookies/ 登录态(免重复登录)
    stream_media: bool = True,    # 音视频(mp4/mkv/mp3/flac/m3u8 等)走流式下载技能
    on_stage: Optional[Callable[[str, str], None]] = None,  # 阶段回调(stage, message)
    is_cancelled: Optional[Callable[[], bool]] = None,      # 协作式取消回调
    verbose: bool = True,
) -> TaskResult:
    """核心入口:检索(或给定种子 URL)→ 分析候选 → 下载(必要时 Agent 兜底)。

    file_types=None 时按查询关键词自动推断(壁纸→图片类, 投影→schematic 类等)。
    on_stage/is_cancelled 供 API 层上报进度与支持取消;取消时返回 success=False,
    error="cancelled"。
    """
    file_types = _infer_file_types(query, file_types)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    probe = FileProbe(out_dir, file_types)
    result = TaskResult(success=False)

    # ---------- 1. 种子 = 检索结果 + 显式给定 URL ----------
    queue: deque[tuple[str, int]] = deque()
    if seed_urls:
        for u in seed_urls:
            queue.append((u, 0))
    if query:
        if verbose:
            print(f"🔎 检索: {query!r}")
        _emit(on_stage, "search", f"检索 {query!r}")
        _check_cancel(is_cancelled)
        candidates = search_engines(query, engines=engines, per_engine=8, probe=False,
                                    limit=max_candidates)
        for c in candidates:
            queue.append((c.url, 0))
    if not queue:
        result.error = "没有可分析的起点(检索无结果且未给 seed_urls)"
        return result

    # ---------- 2. 逐候选分析:直链走快路径,交互页进 Agent ----------
    visited: set[str] = set()
    direct_urls: list[str] = []
    agent_candidates: list[tuple[str, str]] = []
    pan_links: list[str] = []
    analyzed = 0
    _emit(on_stage, "analyze", f"开始分析 {len(queue)} 个候选")

    while queue and analyzed < _MAX_ANALYZE and len(direct_urls) < 3:
        _check_cancel(is_cancelled)
        url, _depth = queue.popleft()
        if url in visited:
            continue
        visited.add(url)
        analyzed += 1
        if verbose:
            print(f"\n[{analyzed}] 分析 {url[:95]}")
        analysis = analyze_page(url, probe=True)
        cls = analysis.page_class
        if verbose:
            print(f"    分级: {cls.value} ({analysis.reason})")

        if cls == PageClass.DIRECT_FILE:
            if not file_types or _ext_match(url, file_types):
                direct_urls.append(url)
            continue

        best = analysis.best_resources

        if cls == PageClass.DOWNLOAD_PAGE:
            files = [r.url for r in best
                     if r.kind == "direct_file"
                     and (not file_types or r.file_ext.lower() in file_types)]
            if files:
                direct_urls.extend(files)
                continue
            # 详情页带下载按钮但无直链 → 交互操作(点击/登录),交给 Agent
            if any(r.kind == "download_button" for r in best):
                agent_candidates.append((url, "详情页带下载按钮"))
                continue

        if cls in _AGENT_CLASSES:
            if (url, cls.value) not in agent_candidates:
                agent_candidates.append((url, analysis.reason or cls.value))

        for r in best:
            if r.kind == "pan_share" and r.url not in pan_links:
                pan_links.append(r.url)

    # ---------- 3. 快路径:直链下载 ----------
    _emit(on_stage, "download", f"直链下载 {len(dict.fromkeys(direct_urls))} 个候选")
    for url in dict.fromkeys(direct_urls):
        _check_cancel(is_cancelled)
        if verbose:
            print(f"⬇️  下载直链: {url[:100]}")
        dl = _download_one(url, out_dir, file_types, stream_media, on_stage,
                           is_cancelled)
        if dl:
            if security_scan:
                verdict, note = _security_gate(dl)
                if verdict in ("infected", "suspicious"):
                    if verbose:
                        print(f"🛡️  安全扫描未通过({verdict}): {note[:120]} → 已删除,尝试下一个")
                    continue
                if verdict == "unknown" and verbose:
                    print("🛡️  安全扫描: 无杀毒引擎(仅启发式覆盖),已放行并标注 unknown")
            _emit(on_stage, "scan", f"安全扫描通过: {Path(dl).name}")
            result.files.append(dl)
            result.sources.append(url)
            if verbose:
                print(f"✅ 已下载: {dl}")
            if probe():
                result.success = True
                result.summary = f"直链下载成功: {dl}"
                return result
        elif verbose:
            print("⚠️  直链下载失败,尝试下一个")

    # ---------- 4. 慢路径:浏览器 Agent 兜底 ----------
    if use_agent_fallback and agent_candidates:
        _emit(on_stage, "agent", f"浏览器 Agent 兜底 {len(agent_candidates)} 个候选")
        tried = 0
        for url, reason in agent_candidates:
            if tried >= _MAX_AGENT_ATTEMPTS:
                break
            tried += 1
            _check_cancel(is_cancelled)
            if verbose:
                print(f"\n🤖 浏览器 Agent 处理: {url[:90]} ({reason})")
            ok = _agent_fetch(url, file_types, out_dir, login_email, username,
                              verbose, reuse_cookies, is_cancelled)
            if ok:
                result.files = probe.found_files()
                if security_scan and result.files:
                    kept = []
                    for f in result.files:
                        verdict, note = _security_gate(f)
                        if verdict in ("infected", "suspicious"):
                            if verbose:
                                print(f"🛡️  Agent 落地文件未过安全扫描({verdict}): {f} → 已删除")
                            continue
                        kept.append(f)
                    result.files = kept
                    if not kept:
                        if verbose:
                            print("🛡️  Agent 落地文件全部未过安全扫描,判定失败")
                        continue
                result.sources.append(url)
                result.success = True
                result.summary = f"Agent 路径完成,文件落地 {len(result.files)} 个"
                return result

    result.files = probe.found_files()
    result.sources.extend(pan_links)
    result.error = "所有候选均未获取到资源文件"
    if result.files:
        result.success = True
        result.summary = f"已获得 {len(result.files)} 个文件(部分成功)"
    elif pan_links and verbose:
        print(f"\n☁️  发现网盘分享链接 {len(pan_links)} 个(网盘解析为后续阶段):")
        for u in pan_links[:5]:
            print(f"     {u[:110]}")
    return result


def _ext_match(url: str, file_types: tuple[str, ...]) -> bool:
    low = url.lower()
    return any(low.endswith(e) for e in file_types)


def _is_media_types(file_types: tuple[str, ...]) -> bool:
    """file_types 是否包含音视频媒体类型(流式下载分流条件)。"""
    from skills.streaming import MEDIA_EXTS

    return any(ft.lower() in MEDIA_EXTS for ft in (file_types or ()))


def _download_one(url: str, out_dir: Path, file_types: tuple[str, ...],
                  stream_media: bool = True,
                  on_stage: Optional[Callable[[str, str], None]] = None,
                  is_cancelled: Optional[Callable[[], bool]] = None) -> Optional[str]:
    """下载单个直链。音视频大文件走 skills.streaming 流式下载,其余走 delivery。"""
    if stream_media and _is_media_types(file_types):
        from skills.streaming import stream_download

        _emit(on_stage, "download", f"流式下载(音视频): {url[:90]}")
        last_pct = [0]

        def on_progress(done: int, total: int) -> None:
            if total > 0:
                pct = int(done * 100 / total)
                if pct - last_pct[0] >= 5 or pct == 100:
                    last_pct[0] = pct
                    _emit(on_stage, "download", f"流式下载 {pct}% ({done}/{total} B)")

        r = stream_download(url, out_dir, on_progress=on_progress)
        return r.path if r.ok else None

    from delivery import download

    expected_ext = ""
    for ext in file_types:
        if url.lower().endswith(ext):
            expected_ext = ext
            break
    r = download(url, out_dir, expected_ext=expected_ext, min_size=100)
    if r.ok:
        return r.path
    return None


def _security_gate(path: str) -> tuple[str, str]:
    """下载查毒闸门:扫描文件,infected/suspicious 时删除并返回 (verdict, 摘要)。

    引擎缺失时扫描器返回 verdict=unknown(ok=True)→ 放行但诚实标注覆盖不足,
    不阻塞下载;只有确认恶意/可疑才拦截。
    """
    from skills.security import scan_file

    try:
        r = scan_file(path)
    except Exception as e:
        return "unknown", f"扫描异常(放行): {type(e).__name__}: {str(e)[:120]}"
    if r.verdict in ("infected", "suspicious"):
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass
    return r.verdict, r.summary


def _agent_fetch(url: str, file_types: tuple[str, ...], out_dir: Path,
                 login_email: str = "", username: str = "",
                 verbose: bool = True, reuse_cookies: bool = True,
                 is_cancelled: Optional[Callable[[], bool]] = None) -> bool:
    """浏览器 Agent:访问页面 →(注册/登录)→ 定位下载 → 下载。

    reuse_cookies=True 时按站点域复用 accounts/cookies/ 的登录态
    (与 run_account.py 同一存储,免重复登录),Agent 成功后回存刷新。
    """
    import secrets
    import string

    from urllib.parse import urlsplit

    from agent import AccountAgent
    from agent.browser import BrowserSession
    from agent.cookies import cookie_path, has_cookies
    from agent.llm import LLMClient

    ck = cookie_path(urlsplit(url).netloc.lower(), login_email)
    ck_path = ck if (reuse_cookies and has_cookies(ck)) else None

    exts = " / ".join(file_types)
    # 密码由任务层生成注入(不让 LLM 自己编,减少表单循环)并持久化,防止账号丢失
    import json as _json

    alphabet = string.ascii_letters + string.digits
    password = "".join(secrets.choice(alphabet) for _ in range(14))
    if login_email:
        creds_path = Path("accounts/credentials.json")
        creds = {}
        if creds_path.exists():
            try:
                creds = _json.loads(creds_path.read_text(encoding="utf-8"))
            except Exception:
                creds = {}
        key = f"schematics-{username or 'default'}"
        creds[key] = {"email": login_email, "username": username, "password": password}
        try:
            creds_path.parent.mkdir(parents=True, exist_ok=True)
            creds_path.write_text(_json.dumps(creds, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    account_hint = ""
    if login_email:
        account_hint = (
            f"\n账号信息: 注册邮箱={login_email}"
            + (f",用户名={username}" if username else "")
            + f",密码={password}(用于注册/登录,请原样输入,不要修改);"
            + "邮箱验证码/安全链接用 mail_code 工具自动收取。"
        )
    else:
        account_hint = (
            "\n注意: 本次没有可用的注册邮箱。若页面需要登录/注册,用 human 工具请求人工介入,"
            "不要自行注册新账号。"
        )
    goal = (
        f"访问 {url} 寻找可下载的 {'/'.join(file_types)} 文件。\n"
        f"步骤: 1) 打开页面;若被 Cloudflare 校验,用 wait 等待通过;"
        f"若需登录/注册,按账号信息完成(见下方;若无邮箱,用 human 请求人工介入);"
        f"2) 用 analyze_page 提取下载链接;"
        f"3) 找到 {exts} 直链或下载按钮:若按钮文本含 Download/下载,直接点击它"
        f"(浏览器会自动捕获下载文件到 {out_dir});若拿到直链 URL,用 download 工具下载;"
        f"4) 文件落地(大小>0)才算完成,直接 done(success=true)。\n"
        f"若需要人工介入(验证码过不去/需要付费),用 human 工具请求。"
        f"{account_hint}"
    )
    try:
        _check_cancel(is_cancelled)
        with BrowserSession(headless=True, downloads_dir=out_dir,
                            cookies_path=ck_path) as session:
            agent = AccountAgent(session=session, llm=LLMClient(), goal=goal,
                                 allowed_domain="", max_steps=60)
            session.goto(url)
            res = agent.run()
            _check_cancel(is_cancelled)
            if res.success and reuse_cookies:
                try:
                    session.save_cookies(ck)   # BrowserSession 方法,内部取 context.storage_state
                    if verbose:
                        print(f"🍪 已回存登录态 Cookie → {ck.name}")
                except Exception as e:
                    if verbose:
                        print(f"⚠️  Cookie 保存失败: {type(e).__name__}: {str(e)[:100]}")
            return res.success
    except TaskCancelled:
        raise
    except Exception as e:
        if verbose:
            print(f"Agent 执行异常: {type(e).__name__}: {str(e)[:150]}")
        return False
