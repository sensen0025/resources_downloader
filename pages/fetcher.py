"""页面抓取 — 轻量 HTTP 抓取 + 内存 TTL 缓存。

对齐 firecrawl「引擎能力矩阵」里的 fetch 引擎:只负责拿回 HTML,
解析交给 extractor。可选 curl_cffi(装了才用)做 TLS 指纹模仿 ——
对 Cloudflare 轻防护站有效;重防护站走浏览器 Agent(agent/ 层)。
"""

from __future__ import annotations

import re
import threading
import time
import urllib.parse
from typing import Optional

import requests

from proxy import apply_proxies, proxies

from .models import PageInfo

__all__ = ["fetch_page", "clear_cache", "FETCH_MAX_BYTES"]

FETCH_MAX_BYTES = 3_000_000  # 3MB 上限,防内存爆炸
TTL_SECONDS = 300.0
_CACHE: dict[str, PageInfo] = {}
_CACHE_LOCK = threading.Lock()

# 搜索引擎中转链接(so.com/link?m=… 等)返回的不是 HTTP 3xx,而是 200 的
# JS/meta-refresh 跳转存根(<script>window.location.replace("…")</script>),
# requests 不会自动跟随 → 页面分析只会看到 252 字节的存根。这里手工跟随。
_REDIRECT_STUB_RE = re.compile(
    r'window\.location\.(?:replace|href)\s*=\s*["\']([^"\']+)["\']'
    r"|<meta\s+http-equiv=[\"']?refresh[\"']?[^>]*content=[\"']?[^\"']*?url\s*=\s*[\"']?([^\"'>\s]+)",
    re.I,
)
_MAX_STUB_HOPS = 5

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_session = requests.Session()
_session.headers.update(_HEADERS)
apply_proxies(_session)  # VPN/代理:页面抓取走 RH_PROXY_URL / 标准环境变量

# 可选 TLS 指纹模仿(curl_cffi 装了才可用,yt-dlp impersonate 同款)
try:  # pragma: no cover - 依赖可选
    from curl_cffi import requests as cffi_requests

    _cffi = cffi_requests
except ImportError:
    _cffi = None


def _title_of(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if not m:
        return ""
    t = re.sub(r"<[^>]+>", "", m.group(1))
    import html as h

    return re.sub(r"\s+", " ", h.unescape(t)).strip()[:300]


def fetch_page(url: str, use_cache: bool = True, timeout: float = 12.0,
               impersonate: bool = False) -> PageInfo:
    """抓取页面 HTML(带缓存)。impersonate=True 时优先用 curl_cffi 模拟 Chrome。"""
    now = time.monotonic()
    if use_cache:
        with _CACHE_LOCK:
            cached = _CACHE.get(url)
        if cached and now - cached.fetched_at < TTL_SECONDS:
            return cached

    info = _fetch_network(url, timeout, impersonate)
    if use_cache and info.status in (200, 206):
        with _CACHE_LOCK:
            _CACHE[url] = info
            if len(_CACHE) > 500:
                _CACHE.clear()
    return info


def _follow_stub(resp: requests.Response, timeout: float) -> requests.Response:
    """跟随 JS/meta-refresh 跳转存根,返回最终响应(最多 _MAX_STUB_HOPS 跳)。"""
    for _ in range(_MAX_STUB_HOPS):
        ct = (resp.headers.get("Content-Type", "") or "").lower()
        if "text/html" not in ct and resp.status_code != 200:
            return resp
        html = resp.text[:3000]
        m = _REDIRECT_STUB_RE.search(html)
        if not m:
            return resp
        target = (m.group(1) or m.group(2) or "").strip()
        if not target or target.startswith(("javascript:", "data:", "#")):
            return resp
        target = urllib.parse.urljoin(resp.url, target)
        if not target.startswith(("http://", "https://")):
            return resp
        if target == resp.url:
            return resp
        try:
            resp = _session.get(target, timeout=timeout, allow_redirects=True)
        except requests.RequestException:
            return resp
    return resp


def _fetch_network(url: str, timeout: float, impersonate: bool) -> PageInfo:
    try:
        if impersonate and _cffi is not None:
            resp = _cffi.get(url, impersonate="chrome", timeout=timeout,
                             proxies=proxies() or None,
                             headers={"Accept-Language": "zh-CN,zh;q=0.9"})
        else:
            resp = _session.get(url, timeout=timeout, allow_redirects=True)
    except requests.exceptions.SSLError:
        # 部分站 https 配置损坏/被墙,只提供 http(如 80ge.info CDN)→ 回退 http
        if url.startswith("https://"):
            http_url = "http://" + url[len("https://"):]
            try:
                resp = _session.get(http_url, timeout=timeout, allow_redirects=True)
            except requests.RequestException as e:
                return PageInfo(url=url, error=f"{type(e).__name__}: {str(e)[:120]}",
                                fetched_at=time.monotonic())
        else:
            raise
    except requests.RequestException as e:
        return PageInfo(url=url, error=f"{type(e).__name__}: {str(e)[:120]}",
                        fetched_at=time.monotonic())
    try:
        resp = _follow_stub(resp, timeout)  # JS/meta-refresh 中转跳转
    except Exception:
        pass
    html = ""
    # 流式读取,限制大小
    if "text/html" in (resp.headers.get("Content-Type", "") or "").lower() or resp.status_code == 200:
        try:
            # 编码修复:中文站常不声明 charset(GBK/UTF-8 都有),requests 按 RFC
            # 默认 ISO-8859-1 解码 → 全文乱码 → 下载链接(锚文本"下载")/标题全失效。
            # 仅在 Header 无 charset(回落到 latin-1)时用 chardet 还原真实编码。
            enc = getattr(resp, "encoding", "") or ""
            if not enc or enc.lower() in ("iso-8859-1", "latin-1", "us-ascii"):
                apparent = getattr(resp, "apparent_encoding", "") or ""
                if apparent:
                    resp.encoding = apparent
            html = resp.text[:FETCH_MAX_BYTES]
        except Exception:
            html = ""
    return PageInfo(
        url=resp.url or url,   # 存根跳转后用最终 URL 作解析基准(相对链接)
        status=resp.status_code,
        final_url=resp.url or url,
        content_type=resp.headers.get("Content-Type", ""),
        title=_title_of(html) if html else "",
        html=html,
        size=len(html),
        fetched_at=time.monotonic(),
    )


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
