"""万能下载器 — 一个入口搞定:直链 / m3u8 切片流 / Cloudflare 墙 / 播放页自动找流。

统一调度(顺序):
1. **播放页/聚合页**:URL 是 HTML 页面(非 m3u8/直链)时,先在页面文本/JS 里
   提取 m3u8 流地址(resolve_stream),下载第一个可用的;
2. **Cloudflare**:直连探测失败或响应含 CF 挑战 → Playwright 系统 Chrome 过 CF,
   复用 cf_clearance cookie 重试;
3. **m3u8**:ts 切片用自带合并(零依赖,支持 AES-128);fmp4/合并失败 → ffmpeg 兜底;
4. **直链**:复用 skills/streaming(分段并发 + 断点续传 + 限速 + 进度)。

对齐技能层定位:底层引擎;AI 工具 `universal_download` 在 `ai/skills.py` 注册。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urljoin, urlsplit

import requests

from proxy import proxies

from .cf import browser_cookies, is_cf_challenge
from .ffmpeg_tool import find_ffmpeg, merge_hls_ffmpeg
from skills.streaming import StreamResult, probe_stream, stream_download

__all__ = ["resolve_stream", "universal_download", "is_page_url", "download_page_stream"]

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 页面/JS 里常见的 m3u8 出现形式(引号、反斜杠转义、video source)
_M3U8_PATTERNS = (
    re.compile(r"""["']([^"']+\.m3u8[^"']*)["']"""),
    re.compile(r"https?://[^\s\"'<>\\]+\.m3u8[^\s\"'<>\\]*", re.I),
    re.compile(r"""url\s*[:=]\s*["']([^"']+\.m3u8[^"']*)["']""", re.I),
)
_HTML_TYPES = ("text/html", "application/xhtml+xml")


def is_page_url(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return path.endswith((".html", ".htm", "/", ".php", ".asp", ".aspx", ".jsp"))


def _fetch_page_text(url: str, referer: str = "", cookies: Optional[dict] = None,
                     timeout: float = 20.0) -> str:
    """抓页面文本(前 512KB),失败返回空串。"""
    try:
        with requests.get(url, headers={"User-Agent": _UA, "Referer": referer or url},
                          timeout=timeout, allow_redirects=True, proxies=proxies(),
                          cookies=cookies) as r:
            if r.status_code != 200:
                return ""
            ct = (r.headers.get("Content-Type", "") or "").lower()
            if not any(t in ct for t in _HTML_TYPES):
                return ""
            return r.text[: (512 << 10)]
    except requests.RequestException:
        return ""


def resolve_stream(page_url: str, referer: str = "",
                   cookies: Optional[dict] = None,
                   timeout: float = 20.0) -> list[str]:
    """从播放页/聚合页 HTML 与 JS 里提取候选 m3u8 流地址(支持一级 iframe 探查)。"""
    html = _fetch_page_text(page_url, referer, cookies, timeout)
    if not html:
        return []
    
    found: list[str] = []
    
    # 1. 提取当前页面所有的 m3u8
    for pat in _M3U8_PATTERNS:
        for m in pat.findall(html):
            raw = m if isinstance(m, str) else m[0]
            raw = raw.replace("\\/", "/")
            if raw.startswith("//"):
                raw = "https:" + raw
            abs_url = urljoin(page_url, raw)
            if abs_url.startswith(("http://", "https://")) and abs_url not in found:
                found.append(abs_url)
                
    # 2. 如果当前页面没有直接找到 m3u8，提取并探查页面内的 iframe
    if not found:
        iframe_srcs = re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.I)
        for src in iframe_srcs:
            if src.startswith("//"):
                src = "https:" + src
            abs_iframe = urljoin(page_url, src)
            # 过滤明显无关的域与广告，只探查含有播放器/解析接口特征的 iframe
            low = abs_iframe.lower()
            if any(k in low for k in ("m3u8", "player", "play", "jx", "dp", "video", "embed", "parse", "view")):
                if abs_iframe.startswith(("http://", "https://")):
                    iframe_html = _fetch_page_text(abs_iframe, referer=page_url, cookies=cookies, timeout=10.0)
                    if iframe_html:
                        for pat in _M3U8_PATTERNS:
                            for m in pat.findall(iframe_html):
                                raw = m if isinstance(m, str) else m[0]
                                raw = raw.replace("\\/", "/")
                                if raw.startswith("//"):
                                    raw = "https:" + raw
                                abs_url = urljoin(abs_iframe, raw)
                                if abs_url.startswith(("http://", "https://")) and abs_url not in found:
                                    found.append(abs_url)
                                    
    return found[:20]


def download_page_stream(page_url: str, dest_dir: str | Path, *,
                         filename: str = "", referer: str = "",
                         cookies: Optional[dict] = None,
                         on_progress: Optional[Callable[[int, int], None]] = None,
                         timeout: float = 30.0) -> StreamResult:
    """播放页:自动找 m3u8 → 下载第一个能落地的流。"""
    candidates = resolve_stream(page_url, referer, cookies, timeout)
    if not candidates:
        return StreamResult(page_url, error="页面中未找到 m3u8 流地址")
    last_err = ""
    for i, m3u8_url in enumerate(candidates):
        r = _download_hls_or_direct(m3u8_url, dest_dir, filename=filename or f"stream_{i}.mp4",
                                    referer=referer or page_url, cookies=cookies,
                                    on_progress=on_progress, timeout=timeout)
        if r.ok:
            return r
        last_err = r.error
    return StreamResult(page_url, error=f"全部流候选下载失败: {last_err[:200]}")


def _download_hls_or_direct(url: str, dest_dir, *, filename="", referer="",
                            cookies=None, on_progress=None, timeout=30.0) -> StreamResult:
    r = stream_download(url, dest_dir, filename=filename, strategy="hls",
                        referer=referer, cookies=cookies, on_progress=on_progress,
                        timeout=timeout)
    if r.ok:
        return r
    if "ffmpeg" in r.error:  # fmp4 或自带合并失败 → ffmpeg 兜底
        dest = Path(dest_dir) / (filename or "stream.mp4")
        ok, msg = merge_hls_ffmpeg(url, dest, referer=referer, cookies=cookies)
        if ok:
            return StreamResult(url, path=msg, size=Path(msg).stat().st_size, ok=True,
                                strategy="hls-ffmpeg")
        r.error = f"{r.error}; ffmpeg 兜底失败: {msg[:150]}"
    return r


def universal_download(
    url: str,
    dest_dir: str | Path,
    *,
    filename: str = "",
    referer: str = "",
    use_browser: bool = True,
    on_progress: Optional[Callable[[int, int], None]] = None,
    on_stage: Optional[Callable[[str, str], None]] = None,
    timeout: float = 30.0,
) -> StreamResult:
    """万能下载入口。返回 StreamResult(复用 streaming 的结果协议)。"""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    cookies: Optional[dict] = None
    is_hls_url = url.lower().split("?")[0].endswith((".m3u8", ".m3u"))

    # ---- 1) 探测(含 CF 检测)----
    probe = probe_stream(url, referer=referer, timeout=timeout)
    cf_blocked = (not probe.ok) or is_cf_challenge(probe.error) or \
                 (probe.status in (403, 503))
    if cf_blocked and use_browser:
        if on_stage:
            on_stage("cf", f"检测到 Cloudflare 挑战,浏览器会话尝试通过: {url[:80]}")
        got = browser_cookies(url)
        if got:
            cookies, _ua = got
            probe = probe_stream(url, referer=referer, cookies=cookies, timeout=timeout)
            if on_stage:
                on_stage("cf", "已取得 cf_clearance,复用 cookie 重试")

    # ---- 2) m3u8 ----
    if is_hls_url or (probe.ok and probe.is_hls):
        return _download_hls_or_direct(url, dest_dir, filename=filename,
                                       referer=referer, cookies=cookies,
                                       on_progress=on_progress, timeout=timeout)

    # ---- 3) HTML 页面(播放页/聚合页)→ 自动找流 ----
    if probe.ok and any(t in (probe.content_type or "").lower() for t in _HTML_TYPES):
        r = download_page_stream(url, dest_dir, filename=filename, referer=referer,
                                 cookies=cookies, on_progress=on_progress,
                                 timeout=timeout)
        if r.ok:
            return r
        # 找不到流 → 落回直链尝试(可能是文件型页面)

    # ---- 4) 直链(流式/分段)----
    return stream_download(url, dest_dir, filename=filename, referer=referer,
                           cookies=cookies, on_progress=on_progress, timeout=timeout)
