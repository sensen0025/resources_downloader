"""B站(bilibili)音视频抓取技能 — 细粒度站点技能,配合浏览器会话使用。

为什么不能靠规则万能下载器:
- B站反爬:裸 requests 抓播放页返回 412,页面里的 window.__playinfo__(DASH m4s
  音视频流 JSON)根本拿不到;
- 只有真实浏览器会话能过 —— 这正是 Agent 手里的资源。

本技能:
- search_videos():B站搜索接口(换源 —— 酷狗/CSDN 登录墙下,B站播放流是可行路径);
- bilibili_download_media():用浏览器会话打开页面(已过反爬)→ 从 DOM 读 __playinfo__
  → 按 mode 下载音频(m4a)或视频(视频流+音频流 ffmpeg 合并为 mp4),带 Referer +
  会话 cookie 下载(DASH m4s);
- @tool bilibili_fetch:注册给 AI,Agent 在 B站页面上调用(prefer 按任务类型自动判定)。

对齐技能层定位:规则引擎给「怎么提取」,AI 给「何时用/用哪个工具」。
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import requests

from proxy import proxies
from skills.core import ToolResult, tool

__all__ = [
    "is_bilibili_url", "search_videos", "extract_playinfo",
    "best_audio_stream", "best_video_stream",
    "download_audio", "download_video", "merge_av_ffmpeg",
    "bilibili_download", "bilibili_download_media", "bilibili_fetch",
]

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_REFERER = "https://www.bilibili.com/"


def is_bilibili_url(url: str) -> bool:
    try:
        host = urlsplit(url).netloc.lower()
    except Exception:
        return False
    return "bilibili.com" in host


# ---------------------------------------------------------------- B站搜索(换源)

def search_videos(keyword: str, limit: int = 6) -> list[dict]:
    """B站视频搜索,返回 [{url, title, author}]。

    裸 API 可用(探测过 200 + 20 条);页面才需要浏览器会话。
    偶发风控返回空 → 先取 buvid3 匿名 cookie 再重试一次。
    """

    def _parse(j: dict) -> list[dict]:
        items = (j.get("data") or {}).get("result") or []
        out = []
        for v in items[:limit]:
            bvid = v.get("bvid")
            if not bvid:
                continue
            title = re.sub(r"<[^>]+>", "", str(v.get("title") or "")).strip()
            out.append({
                "url": f"https://www.bilibili.com/video/{bvid}",
                "title": title[:100],
                "author": str(v.get("author") or ""),
            })
        return out

    def _call(cookies=None) -> list[dict]:
        try:
            r = requests.get(
                "https://api.bilibili.com/x/web-interface/search/type",
                params={"search_type": "video", "keyword": keyword},
                headers={"User-Agent": _UA, "Referer": _REFERER},
                timeout=15, proxies=proxies(), cookies=cookies,
            )
            if r.status_code != 200:
                return []
            return _parse(r.json())
        except Exception:
            return []

    hits = _call()
    if hits:
        return hits
    # 兜底:先访问一次主页拿 buvid3 匿名标识,再带 cookie 重试
    try:
        sess = requests.Session()
        sess.get("https://www.bilibili.com/", headers={"User-Agent": _UA},
                 timeout=15, proxies=proxies())
        ck = {c.name: c.value for c in sess.cookies}
        return _call(cookies=ck or None)
    except Exception:
        return []


# ---------------------------------------------------------------- playinfo 提取

def extract_playinfo(page) -> dict:
    """从浏览器页面读 window.__playinfo__(页面须已加载完)。

    先取 JS 全局(window.__playinfo__),失败再正则扫 HTML(SPA 变体)。
    """
    try:
        pi = page.evaluate("() => (window.__playinfo__ || null)")
        if isinstance(pi, dict):
            return pi
    except Exception:
        pass
    try:
        html = page.content()
        m = re.search(r"window\.__playinfo__=(\{.*?\})</script>", html, re.DOTALL)
        if m:
            return json.loads(m.group(1))
    except Exception:
        pass
    return {}


def _dash_of(playinfo: dict) -> dict:
    """DASH 流根:普通视频页在 data.dash;番剧(bangumi)页结构不一 ——
    pgc API 在 result.dash,页面 __playinfo__ 在 result.video_info.dash。

    线上事故:『凡人修仙传 第10集』→ bangumi/play/ep733325,页面 playinfo 的
    DASH 藏在 result.video_info.dash,旧代码只认 data.dash → 流全空、第10集失败。
    data/result 两个根,每个根再查 dash 与 video_info.dash 两处,谁有流用谁。
    """
    if not isinstance(playinfo, dict):
        return {}
    for root in ("data", "result"):
        d = playinfo.get(root)
        if not isinstance(d, dict):
            continue
        for cand in (d.get("dash"), (d.get("video_info") or {}).get("dash")):
            if isinstance(cand, dict) and cand:
                return cand
    return {}


def _play_root(playinfo: dict) -> dict:
    """playinfo 的 data/result 根(取非空者),用于 is_preview 等元信息。"""
    if not isinstance(playinfo, dict):
        return {}
    for root in ("data", "result"):
        d = playinfo.get(root)
        if isinstance(d, dict) and d:
            return d
    return {}


def _is_preview_playinfo(playinfo: dict) -> bool:
    """大会员试看标记:result/data 根或 video_info 里的 is_preview=1。"""
    root = _play_root(playinfo)
    if not root:
        return False
    if root.get("is_preview"):
        return True
    vi = root.get("video_info")
    return bool(isinstance(vi, dict) and vi.get("is_preview"))


def _is_bangumi_url(url: str) -> bool:
    return bool(url) and "/bangumi/play/" in url


def _has_usable_streams(playinfo: dict) -> bool:
    """DASH 里是否真有可下载的流(baseUrl/backupUrl 至少一个非空)。

    线上事故:番剧页 video_info.dash 的条目 baseUrl 全是 None(占位流),
    best_video_stream 取得到"流"但下载必败 —— 必须验证 URL 真实存在。
    """
    dash = _dash_of(playinfo)
    for group in ("video", "audio"):
        for st in dash.get(group) or []:
            if st.get("baseUrl") or st.get("backupUrl"):
                return True
    return False


def _pgc_playurl_api(url: str, cookies: Optional[dict] = None) -> dict:
    """番剧页 playinfo 拿不到 DASH 时,调 pgc playurl API 兜底。

    与 __playinfo__ 同构(根 result,含 dash),失败返回 {}。
    带上会话 cookie(登录/大会员状态决定清晰度与是否试看)。
    """
    m = re.search(r"/bangumi/play/ep(\d+)", url or "")
    if not m:
        return {}
    try:
        import requests

        from proxy import proxies

        r = requests.get(
            "https://api.bilibili.com/pgc/player/web/playurl",
            params={"ep_id": m.group(1), "qn": "80", "fnval": "16", "fourk": "1"},
            headers={"User-Agent": _UA, "Referer": url},
            timeout=20, proxies=proxies(), cookies=cookies,
        )
        j = r.json()
        if j.get("code") == 0 and j.get("result"):
            return j
    except Exception:
        pass
    return {}


def best_audio_stream(playinfo: dict) -> Optional[dict]:
    """DASH 音频流按码率降序取最佳(DASH 结构:data/result.dash.audio[])。"""
    dash = _dash_of(playinfo)
    audio = list(dash.get("audio") or [])
    if not audio:
        return None
    audio.sort(key=lambda a: -(int(a.get("bandwidth") or 0)))
    return audio[0]


def best_video_stream(playinfo: dict) -> Optional[dict]:
    dash = _dash_of(playinfo)
    video = list(dash.get("video") or [])
    if not video:
        return None
    video.sort(key=lambda a: -(int(a.get("bandwidth") or 0)))
    return video[0]


# ---------------------------------------------------------------- 下载

def download_stream(stream: dict, dest_dir: Path, filename: str = "",
                    cookies: Optional[dict] = None, kind: str = "audio") -> tuple[Optional[Path], int]:
    """下载 DASH 音/视频流(baseUrl),Referer 必须 B站;403 时依次试 backupUrl。

    返回 (path, size);失败 (None, 0)。
    """
    urls = [stream.get("baseUrl")] + list(stream.get("backupUrl") or [])
    for u in urls:
        if not u:
            continue
        try:
            with requests.get(
                u, headers={"User-Agent": _UA, "Referer": _REFERER},
                timeout=120, stream=True, proxies=proxies(), cookies=cookies,
            ) as r:
                if r.status_code != 200:
                    continue
                if not filename:
                    stem = stream.get("codecs") or kind
                    filename = f"bilibili_{kind}_{stream.get('id', '')}_{int(time.time())}.m4s"
                path = dest_dir / filename
                size = 0
                with open(path, "wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        f.write(chunk)
                        size += len(chunk)
                if size > 0:
                    return path, size
        except requests.RequestException:
            continue
    return None, 0


def download_audio(stream: dict, dest_dir: Path, filename: str = "",
                   cookies: Optional[dict] = None) -> tuple[Optional[Path], int]:
    """DASH 音频流 → .m4a(旧接口保持兼容)。"""
    path, size = download_stream(stream, dest_dir, filename=filename,
                                 cookies=cookies, kind="audio")
    if path and path.suffix.lower() != ".m4a":
        p2 = path.with_suffix(".m4a")
        try:
            path.replace(p2)
            path = p2
        except OSError:
            pass
    return path, size


def download_video(stream: dict, dest_dir: Path, filename: str = "",
                   cookies: Optional[dict] = None) -> tuple[Optional[Path], int]:
    """DASH 视频流 → .mp4(fMP4,直接可播)。"""
    path, size = download_stream(stream, dest_dir, filename=filename,
                                 cookies=cookies, kind="video")
    if path and path.suffix.lower() != ".mp4":
        p2 = path.with_suffix(".mp4")
        try:
            path.replace(p2)
            path = p2
        except OSError:
            pass
    return path, size


def merge_av_ffmpeg(video_path: Path, audio_path: Path, dest: Path, *,
                    timeout: float = 600.0) -> tuple[bool, str]:
    """ffmpeg 合并本地视频流+音频流 → dest(mp4,-c copy 不重编码)。

    返回 (ok, path_or_error)。ffmpeg 未装或合并失败时返回错误(调用方降级为视频-only)。
    注意:dest 必须与输入文件不同名(ffmpeg 输出=输入会直接失败)。
    """
    from skills.universal.ffmpeg_tool import find_ffmpeg

    ff = find_ffmpeg()
    if not ff:
        return False, "ffmpeg 未安装(仅音频/视频流可单独交付)"
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ff, "-y", "-loglevel", "error",
           "-i", str(video_path), "-i", str(audio_path),
           "-c", "copy", "-movflags", "+faststart", str(dest)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"ffmpeg 合并超时(>{timeout}s)"
    except Exception as e:
        return False, f"ffmpeg 合并执行失败: {type(e).__name__}: {str(e)[:120]}"
    if proc.returncode != 0:
        return False, f"ffmpeg 合并退出码 {proc.returncode}: {(proc.stderr or proc.stdout or '')[:200]}"
    if not dest.exists() or dest.stat().st_size == 0:
        return False, "ffmpeg 合并输出为空文件"
    return True, str(dest)


def bilibili_download_media(url: str, dest_dir: str | Path, *,
                            session=None, mode: str = "auto",
                            filename: str = "") -> dict:
    """浏览器会话打开页面 → 读 playinfo → 按 mode 下载音/视频。

    mode: "audio" 只下音频 m4a;"video" 下视频流+音频流合并 mp4(无视频流降级音频;
    合并失败保留视频流);"auto" 同 "video"。session=None 时无法过反爬 → 返回错误。
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    if session is None or getattr(session, "page", None) is None:
        return {"ok": False, "error": "B站反爬(412),必须用浏览器会话打开页面(bilibili_fetch 工具会自动处理)"}
    page = session.page
    try:
        cur = (page.url or "").split("?")[0]
        tgt = url.split("?")[0]
        if cur != tgt:
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(2500)  # 等 JS 注入 __playinfo__
    except Exception as e:
        return {"ok": False, "error": f"打开页面失败: {type(e).__name__}: {str(e)[:120]}"}

    playinfo = extract_playinfo(page)
    if not playinfo:
        return {"ok": False, "error": "页面未找到 playinfo(B站可能要求登录/分区限制/页面布局变更)"}

    # 番剧(bangumi)集:页面 playinfo 的 video_info.dash 常是 baseUrl=None 的占位流,
    # pgc playurl API(带会话 cookie)才是权威源 —— 有可用流就替换;
    # 非番剧页面流不可用时也试 API 兜底(非番剧 URL 会直接返回空,无副作用)。
    try:
        ck_d = {c["name"]: c["value"] for c in session.context.cookies()} \
            if (session is not None and session.context is not None) else None
    except Exception:
        ck_d = None
    if _is_bangumi_url(url) or not _has_usable_streams(playinfo):
        api_pi = _pgc_playurl_api(url, cookies=ck_d)
        if _has_usable_streams(api_pi):
            playinfo = api_pi

    # 试看标记:pgc 结构的 result.is_preview / video_info.is_preview(1=大会员试看,非完整正片)
    _preview = _is_preview_playinfo(playinfo)
    _preview_note = "该集为大会员试看版(可能非完整正片)" if _preview else ""

    cookies = None
    try:
        if session.context is not None:
            cookies = {c["name"]: c["value"] for c in session.context.cookies()}
    except Exception:
        cookies = None

    if mode == "audio":
        stream = best_audio_stream(playinfo)
        if not stream:
            return {"ok": False, "error": "无音频流(该视频可能没有音频轨)"}
        path, size = download_audio(stream, dest, filename=filename, cookies=cookies)
        if not path:
            return {"ok": False, "error": "音频流下载失败(CDN 拒绝,可稍后重试)"}
        return {"ok": True, "path": str(path), "size": size,
                "codecs": stream.get("codecs", ""), "mode": "audio",
                "note": _preview_note or ""}

    # video / auto:视频流 + 音频流合并
    vstream = best_video_stream(playinfo)
    if not vstream:
        # 纯音频稿件(无视频轨)→ 降级音频
        astream = best_audio_stream(playinfo)
        if not astream:
            return {"ok": False, "error": "无音视频流(番剧大会员锁定集仅试看时也可能拿不到完整流)"}
        path, size = download_audio(astream, dest, filename=filename, cookies=cookies)
        if not path:
            return {"ok": False, "error": "音频流下载失败(CDN 拒绝,可稍后重试)"}
        return {"ok": True, "path": str(path), "size": size,
                "codecs": astream.get("codecs", ""), "mode": "audio",
                "note": ("该稿件无视频轨,已降级为音频"
                         + (f"; {_preview_note}" if _preview_note else ""))}
    vpath, vsize = download_video(vstream, dest, cookies=cookies)
    if not vpath:
        return {"ok": False, "error": "视频流下载失败(CDN 拒绝,可稍后重试)"}
    astream = best_audio_stream(playinfo)
    if astream:
        apath, _ = download_audio(astream, dest, cookies=cookies)
        if apath:
            # 合并输出必须与输入视频流不同名(download_video 已把 .m4s 存为 .mp4,
            # 同名输出会让 ffmpeg "输出=输入" 直接失败 —— 线上事故);
            # 指定 filename 时(列表条目 序号_标题)用它命名合并产物
            if filename:
                out = dest / (filename if filename.lower().endswith(".mp4") else f"{filename}.mp4")
            else:
                out = dest / f"{Path(vpath).stem}_merged.mp4"
            ok, msg = merge_av_ffmpeg(Path(vpath), Path(apath), out)
            if ok:
                for p in (Path(vpath), Path(apath)):
                    try:
                        p.unlink(missing_ok=True)
                    except OSError:
                        pass
                return {"ok": True, "path": msg, "size": out.stat().st_size,
                        "codecs": vstream.get("codecs", ""), "mode": "video", "merged": True,
                        "note": _preview_note or ""}
            # 合并失败:保留视频流并报告
            if filename:
                vp = Path(vpath)
                vp2 = dest / (filename if filename.lower().endswith(".mp4") else f"{filename}.mp4")
                try:
                    vp.rename(vp2)
                    vpath = vp2
                except OSError:
                    pass
            return {"ok": True, "path": str(vpath), "size": vsize,
                    "codecs": vstream.get("codecs", ""), "mode": "video",
                    "note": (f"音视频合并失败({msg[:60]}),已保留视频流"
                             + (f"; {_preview_note}" if _preview_note else ""))}
    if filename:
        vp = Path(vpath)
        vp2 = dest / (filename if filename.lower().endswith(".mp4") else f"{filename}.mp4")
        try:
            vp.rename(vp2)
            vpath = vp2
        except OSError:
            pass
    return {"ok": True, "path": str(vpath), "size": vsize,
            "codecs": vstream.get("codecs", ""), "mode": "video",
            "note": ("无音频轨" + (f"; {_preview_note}" if _preview_note else ""))}


def bilibili_download(url: str, dest_dir: str | Path, *,
                      session=None, prefer_audio: bool = True,
                      filename: str = "") -> dict:
    """兼容旧接口:按 prefer_audio 决定音频/视频下载。"""
    return bilibili_download_media(url, dest_dir, session=session,
                                   mode="audio" if prefer_audio else "video",
                                   filename=filename)


# ---------------------------------------------------------------- 工具(AI 可调用)

@tool(
    "bilibili_fetch",
    "从 B站(bilibili.com)视频/番剧页下载音视频。B站有反爬(裸 HTTP 抓页面返回 412),"
    "音视频流地址藏在页面 window.__playinfo__ 的 DASH JSON 里;"
    "本工具用当前浏览器会话打开页面(已过反爬),从 DOM 读 playinfo,按 prefer 下载:"
    "prefer=video(默认,视频/资源任务)下载视频流并与音频合并为 mp4;"
    "prefer=audio 只下载音频 m4a(音乐/铃声任务)。带 Referer 与会话 cookie 下载。"
    "遇到 bilibili.com/video 或 /bangumi 页面时优先用本工具,不要点页面上的「下载」按钮"
    "(那是 APP 客户端),也不要尝试裸 HTTP 抓页面。",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "B站视频页 URL,如 https://www.bilibili.com/video/BVxxxx"},
            "filename": {"type": "string", "description": "输出文件名(缺省自动生成)"},
            "prefer": {"type": "string", "enum": ["auto", "video", "audio"],
                       "description": "auto=按任务类型自动判定(视频任务→视频+合并);video=视频;audio=仅音频"},
        },
        "required": ["url"],
    },
    category="execute",
    timeout_ms=600_000,
    concurrency_safe=False,
)
def bilibili_fetch(url: str, filename: str = "", prefer: str = "auto", ctx=None) -> ToolResult:
    if not is_bilibili_url(url):
        return ToolResult.failure("不是 bilibili 页面 URL")
    dest_dir = "downloads"
    mode = prefer if prefer in ("audio", "video") else "auto"
    if ctx is not None:
        task = getattr(ctx, "task", None)
        out = getattr(task, "out_dir", None)
        if out:
            dest_dir = str(out)
        if mode == "auto" and task is not None:
            fts = {str(f).lower() for f in (getattr(task, "file_types", None) or ())}
            _VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv", ".m3u8", ".ts"}
            _AUDIO_EXTS = {".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".opus"}
            if fts & _VIDEO_EXTS:
                mode = "video"
            elif fts & _AUDIO_EXTS:
                mode = "audio"
            else:
                mode = "video"  # 默认:资源类查询优先完整视频
    session = getattr(ctx, "session", None) if ctx is not None else None
    r = bilibili_download_media(url, dest_dir, session=session, mode=mode,
                                filename=filename)
    if r.get("ok"):
        kind = "B站视频" if r.get("mode") == "video" else "B站音频"
        note = f", {r['note']}" if r.get("note") else ""
        return ToolResult.success(
            f"{kind}下载成功: {r['path']} ({r['size']} 字节, codecs={r.get('codecs','')}{note})",
            data=r,
        )
    return ToolResult.failure(f"B站下载失败: {r.get('error','未知原因')}", data=r)
