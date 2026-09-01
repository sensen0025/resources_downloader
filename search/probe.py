"""可用性探测 — 「筛选可用的」第二步:轻量 HTTP 探测候选 URL。

对齐 firecrawl 引擎瀑布流 + yt-dlp 内容探测:
- 网盘分享域名(pan.baidu.com 等):不联网,直接分类 pan_share;
- 其余:HEAD(重定向跟随)→ 失败/405 降级 GET Range 首字节;
- 按 状态码 + Content-Type + 扩展名 分类:直链文件 / 网页 / 反爬 / 失效 / 不可达;
- 进程内缓存(同 URL 不重复探测),线程安全。
"""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests

from proxy import apply_proxies

from .models import (
    KIND_BLOCKED, KIND_DEAD, KIND_DIRECT_FILE, KIND_PAN_SHARE,
    KIND_UNKNOWN, KIND_UNREACHABLE, KIND_WEBPAGE, ProbeInfo,
)
from .normalize import (
    DIRECT_EXTENSIONS,
    file_ext,
    is_direct_file_url,
    is_pan_share,
)

__all__ = ["probe_url", "probe_many", "PROBE_WEIGHTS", "PROBE_DEFAULT_TIMEOUT"]

PROBE_DEFAULT_TIMEOUT = 8.0

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept": "*/*",
}

# 探测结果对最终排序的加分(在 filter.relevance_score 之上叠加)
PROBE_WEIGHTS = {
    KIND_DIRECT_FILE: +3.0,
    KIND_PAN_SHARE: +2.5,
    KIND_WEBPAGE: 0.0,
    KIND_UNKNOWN: 0.0,
    KIND_BLOCKED: -5.0,
    KIND_UNREACHABLE: -3.0,
    KIND_DEAD: -8.0,
}

_cache: dict[str, ProbeInfo] = {}
_cache_lock = threading.Lock()
_session = requests.Session()
_session.headers.update(_HEADERS)
apply_proxies(_session)  # VPN/代理:检索可用性探测同链路

_CHALLENGE_MARKERS = ("just a moment", "cf-challenge", "cf_chl", "__cf_chl", "verify you are human", "安全验证")


def _classify(status: int, content_type: str, ext: str, body_head: str = "") -> str:
    """纯函数分类(可单测)。"""
    lower_body = (body_head or "").lower()
    # 反爬挑战标记优先判定(与状态码无关)
    if any(m in lower_body for m in _CHALLENGE_MARKERS):
        return KIND_BLOCKED
    if status in (403, 429):
        return KIND_BLOCKED
    if status in (404, 410):
        return KIND_DEAD
    if status not in (200, 206):
        return KIND_UNREACHABLE
    ct = (content_type or "").lower()
    if "text/html" in ct:
        return KIND_WEBPAGE
    if "application/pdf" in ct or ext in DIRECT_EXTENSIONS:
        return KIND_DIRECT_FILE
    if any(k in ct for k in ("octet-stream", "zip", "x-rar", "x-7z", "x-msdownload")):
        return KIND_DIRECT_FILE
    if ct and not ct.startswith("text/") and not ct.startswith("image/"):
        return KIND_DIRECT_FILE
    return KIND_WEBPAGE if "text" in ct else KIND_UNKNOWN


def probe_url(url: str, timeout: float = PROBE_DEFAULT_TIMEOUT) -> ProbeInfo:
    """探测单个 URL。网盘域名走快速通道,其余走网络。"""
    with _cache_lock:
        cached = _cache.get(url)
    if cached is not None:
        return cached

    if is_pan_share(url):
        info = ProbeInfo(status=0, kind=KIND_PAN_SHARE, note="网盘分享页(未联网)")
        _cache_set(url, info)
        return info

    info = _probe_network(url, timeout)
    _cache_set(url, info)
    return info


def _cache_set(url: str, info: ProbeInfo) -> None:
    with _cache_lock:
        _cache[url] = info
        if len(_cache) > 2000:  # 简单上限
            _cache.clear()


def _probe_network(url: str, timeout: float) -> ProbeInfo:
    ext = file_ext(url)
    is_direct = is_direct_file_url(url)  # 权威直链判定(扩展名+主机/路径模式)
    # 1) HEAD
    try:
        r = _session.head(url, allow_redirects=True, timeout=timeout)
        if r.status_code < 400 and r.status_code not in (403, 405, 501):
            ct = r.headers.get("Content-Type", "")
            size = int(r.headers.get("Content-Length") or -1)
            kind = _classify(r.status_code, ct, ext if is_direct else "")
            return ProbeInfo(
                status=r.status_code, content_type=ct, size=size,
                kind=kind, final_url=r.url or url,
            )
        # HEAD 4xx/5xx 不算数(部分反爬站/CDN 拒绝 HEAD 但 GET 正常),
        # 落到 2) 的 GET Range 首字节再判定,避免误标 dead/unreachable。
    except requests.RequestException:
        pass
    # 2) GET Range 首字节(HEAD 失败/被禁)
    try:
        r = _session.get(
            url, allow_redirects=True, timeout=timeout,
            headers={**_HEADERS, "Range": "bytes=0-2047"},
            stream=True,
        )
        head = b""
        try:
            for chunk in r.iter_content(4096):
                head += chunk
                if len(head) >= 2048:
                    break
        finally:
            r.close()
        ct = r.headers.get("Content-Type", "")
        size = int(r.headers.get("Content-Length") or -1)
        kind = _classify(r.status_code, ct, ext if is_direct else "",
                         head.decode("utf-8", "ignore")[:2048])
        return ProbeInfo(
            status=r.status_code, content_type=ct, size=size,
            kind=kind, final_url=r.url or url,
        )
    except requests.RequestException as e:
        return ProbeInfo(kind=KIND_UNREACHABLE, note=f"{type(e).__name__}: {str(e)[:80]}")


def probe_many(urls: list[str], max_workers: int = 8,
               timeout: float = PROBE_DEFAULT_TIMEOUT) -> dict[str, ProbeInfo]:
    """并发探测一批 URL,返回 url -> ProbeInfo。"""
    out: dict[str, ProbeInfo] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(probe_url, u, timeout): u for u in urls}
        for fut in as_completed(futs):
            u = futs[fut]
            try:
                out[u] = fut.result()
            except Exception as e:
                out[u] = ProbeInfo(kind=KIND_UNREACHABLE, note=str(e)[:80])
    return out


def apply_probe_weights(results, probes: dict[str, ProbeInfo]) -> None:
    """把探测结果挂到结果上并按权重修正分数(就地修改)。"""
    for r in results:
        p = probes.get(r.url)
        if p is None:
            continue
        r.probe = p
        r.score += PROBE_WEIGHTS.get(p.kind, 0.0)
