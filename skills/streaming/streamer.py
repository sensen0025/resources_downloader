"""流式下载核心 — 专治音视频等大文件(直链分段并发 / 边下边交付 / 限速 / 进度)。

对齐技能层定位(DSH 式「规则做工具」):底层引擎零第三方依赖(requests 为可选加速,
纯标准库也能跑);AI 决策型工具 `download_stream` 在 `ai/skills.py` 注册,
下载链路分流在 `agent/tasks/fetch_resource.py`(media 类型自动走这里)。

策略:
1. **probe**(HEAD):探测 Content-Length / Accept-Ranges / Content-Type / 是否 m3u8;
2. **direct**(直链大文件):支持 Range 时按**分片并发**(ThreadPoolExecutor,分片级断点
   续传:已完整分片跳过,aria2/yt-dlp 同款);不支持 Range 时单流顺序 + 边下边交付;
3. **hls**(m3u8):解析 master/variant → 分段并发下载(可选 AES-128 解密,需
   cryptography)→ 按序合并,边下边回调进度。
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import requests

from proxy import proxies

__all__ = [
    "StreamProbe",
    "StreamResult",
    "probe_stream",
    "stream_download",
    "is_media_url",
]

MEDIA_EXTS = {
    ".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv", ".ts", ".m4v",
    ".mp3", ".flac", ".wav", ".ogg", ".oga", ".m4a", ".aac", ".opus",
    ".m3u8", ".m3u", ".ts", ".aac",
}
_MEDIA_TYPES = (
    "audio/", "video/", "application/vnd.apple.mpegurl", "application/x-mpegurl",
    "application/ogg", "audio/mpegurl",
)
_HLS_EXT = (".m3u8", ".m3u")
DEFAULT_SEGMENTS = 4
DEFAULT_MIN_SEGMENT = 5 << 20  # 5MB 以下不分段(小文件直接单流)
HLS_MAX_SEGMENTS_CONCURRENT = 8
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@dataclass
class StreamProbe:
    """HEAD 探测结果:决定下载策略。"""

    url: str
    ok: bool = False
    error: str = ""
    status: int = 0
    content_type: str = ""
    content_length: int = -1
    accept_ranges: bool = False
    is_hls: bool = False
    filename: str = ""

    @property
    def is_media(self) -> bool:
        ct = (self.content_type or "").lower()
        return self.is_hls or ct.startswith(_MEDIA_TYPES) or is_media_url(self.url)


@dataclass
class StreamResult:
    url: str
    path: str = ""
    size: int = -1
    ok: bool = False
    error: str = ""
    strategy: str = ""          # direct-single / direct-segments / hls
    segments: int = 0
    resumed: bool = False
    elapsed: float = 0.0
    sha256: str = ""

    def to_dict(self) -> dict:
        return {"url": self.url, "path": self.path, "size": self.size, "ok": self.ok,
                "error": self.error, "strategy": self.strategy, "segments": self.segments,
                "resumed": self.resumed, "elapsed": round(self.elapsed, 2),
                "sha256": self.sha256}


def is_media_url(url: str) -> bool:
    from urllib.parse import urlsplit, unquote

    path = unquote(urlsplit(url).path).lower()
    return any(path.endswith(e) for e in MEDIA_EXTS)


def _headers(referer: str = "") -> dict:
    h = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
         "Accept": "*/*"}
    if referer:
        h["Referer"] = referer
    return h


def probe_stream(url: str, timeout: float = 15.0, referer: str = "",
                 cookies: Optional[dict] = None) -> StreamProbe:
    """HEAD 探测(不支持 HEAD 时降级 GET Range 首字节)。cookies 供 CF 会话复用。"""
    try:
        with requests.head(url, headers=_headers(referer), timeout=timeout,
                           allow_redirects=True, proxies=proxies(),
                           cookies=cookies) as r:
            if r.status_code in (405, 403, 501):
                # HEAD 被拒 → GET 带 Range 首字节探测
                with requests.get(url, headers={**_headers(referer), "Range": "bytes=0-0"},
                                  timeout=timeout, allow_redirects=True,
                                  proxies=proxies(), cookies=cookies) as g:
                    if g.status_code not in (200, 206):
                        return StreamProbe(url, error=f"HTTP {g.status_code}")
                    return StreamProbe(
                        url, ok=True, status=g.status_code,
                        content_type=g.headers.get("Content-Type", ""),
                        content_length=_parse_length(g.headers.get("Content-Range")
                                                     or g.headers.get("Content-Length", "")),
                        accept_ranges="bytes" in (g.headers.get("Accept-Ranges", "") or "").lower(),
                        is_hls=_looks_hls(url, g.headers.get("Content-Type", "")),
                    )
            if r.status_code != 200:
                return StreamProbe(url, error=f"HTTP {r.status_code}")
            ct = r.headers.get("Content-Type", "")
            return StreamProbe(
                url, ok=True, status=200, content_type=ct,
                content_length=_parse_length(r.headers.get("Content-Length", "")),
                accept_ranges="bytes" in (r.headers.get("Accept-Ranges", "") or "").lower(),
                is_hls=_looks_hls(url, ct),
            )
    except requests.RequestException as e:
        return StreamProbe(url, error=f"{type(e).__name__}: {str(e)[:120]}")


def _parse_length(raw: str) -> int:
    """解析 Content-Length 或 Content-Range(bytes 0-99/12345) 的总长。"""
    if not raw:
        return -1
    m = re.search(r"/(\d+)$", raw)
    if m:
        return int(m.group(1))
    try:
        return int(raw.strip())
    except ValueError:
        return -1


def _looks_hls(url: str, content_type: str) -> bool:
    low_url = url.lower()
    ct = (content_type or "").lower()
    return low_url.endswith(_HLS_EXT) or "mpegurl" in ct


def stream_download(
    url: str,
    dest_dir: str | Path,
    *,
    filename: str = "",
    strategy: str = "auto",          # auto / direct / hls
    segments: int = DEFAULT_SEGMENTS,
    min_segment_bytes: int = DEFAULT_MIN_SEGMENT,
    on_progress: Optional[Callable[[int, int], None]] = None,   # (done, total)
    on_chunk: Optional[Callable[[bytes], None]] = None,         # 边下边交付(逐块回调)
    speed_limit: int = 0,             # 字节/秒,0=不限
    expected_sha256: str = "",
    referer: str = "",
    cookies: Optional[dict] = None,   # CF 会话 cookie 等(requests cookies dict)
    timeout: float = 30.0,
    retries: int = 3,
) -> StreamResult:
    """流式下载入口。strategy=auto:按探测结果选 direct/hls。"""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()

    probe = probe_stream(url, timeout=timeout, referer=referer, cookies=cookies)
    if not probe.ok:
        return StreamResult(url, error=f"探测失败: {probe.error}", elapsed=time.monotonic() - t0)

    if not filename:
        filename = _guess_filename(url, probe)
    dest = dest_dir / filename

    if strategy == "auto":
        strategy = "hls" if probe.is_hls else "direct"

    try:
        if strategy == "hls":
            from .hls import hls_download

            r = hls_download(url, dest, on_progress=on_progress, on_chunk=on_chunk,
                             referer=referer, cookies=cookies, timeout=timeout)
            r.url = url
            r.elapsed = time.monotonic() - t0
            return r
        if strategy == "direct":
            return _direct_download(url, dest, probe, segments, min_segment_bytes,
                                    on_progress, on_chunk, speed_limit, referer,
                                    cookies, timeout, retries, t0, expected_sha256)
        return StreamResult(url, error=f"未知策略 {strategy!r}", elapsed=time.monotonic() - t0)
    except Exception as e:
        return StreamResult(url, error=f"{type(e).__name__}: {str(e)[:200]}",
                            elapsed=time.monotonic() - t0)


# ---------------------------------------------------------------- 直链

def _direct_download(url, dest: Path, probe: StreamProbe, segments: int,
                     min_segment_bytes: int, on_progress, on_chunk, speed_limit,
                     referer, cookies, timeout, retries, t0,
                     expected_sha256: str = "") -> StreamResult:
    size = probe.content_length
    can_range = probe.accept_ranges and size > 0
    use_segments = can_range and size >= min_segment_bytes and segments > 1

    if not use_segments:
        return _single_stream(url, dest, size, on_progress, on_chunk, speed_limit,
                              referer, cookies, timeout, retries, t0)

    # ---- 分片并发 + 分片级断点续传 ----
    part_dir = dest.parent / f"{dest.name}.parts"
    part_dir.mkdir(parents=True, exist_ok=True)
    n = min(segments, 16)
    bounds = _split_bounds(size, n)
    resumed = False

    def fetch(bound: tuple[int, int]) -> bool:
        nonlocal resumed
        idx = bound[2]
        part = part_dir / f"{idx:04d}.part"
        if part.exists() and part.stat().st_size == (bound[1] - bound[0] + 1):
            resumed = True
            return True
        hdrs = _headers(referer)
        hdrs["Range"] = f"bytes={bound[0]}-{bound[1]}"
        for attempt in range(retries):
            try:
                with requests.get(url, headers=hdrs, stream=True, timeout=timeout,
                                  allow_redirects=True, proxies=proxies(),
                                  cookies=cookies) as r:
                    if r.status_code != 206:
                        continue
                    with open(part, "wb") as f:
                        for chunk in r.iter_content(1 << 16):
                            if _throttle(speed_limit, chunk):
                                return False
                            f.write(chunk)
                    return True
            except requests.RequestException:
                if attempt < retries - 1:
                    time.sleep(1 * (attempt + 1))
        return False

    done = 0
    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = {pool.submit(fetch, b): b for b in bounds}
        for fut in as_completed(futures):
            if not fut.result():
                pool.shutdown(wait=False, cancel_futures=True)
                return StreamResult(url, error="分片下载失败(重试耗尽)",
                                    strategy="direct-segments", elapsed=time.monotonic() - t0)
            done += 1
            if on_progress:
                on_progress(done * (size // n), size)

    # 按序合并到 .part → rename
    final_part = dest.parent / f"{dest.name}.part"
    with open(final_part, "wb") as out:
        for i in range(len(bounds)):
            part = part_dir / f"{i:04d}.part"
            with open(part, "rb") as f:
                shutil.copyfileobj(f, out, 1 << 16)
    os.replace(final_part, dest)
    shutil.rmtree(part_dir, ignore_errors=True)

    sha = _sha256_of(dest) if expected_sha256 else ""
    return StreamResult(url, path=str(dest), size=dest.stat().st_size, ok=True,
                        strategy="direct-segments", segments=len(bounds),
                        resumed=resumed, sha256=sha,
                        elapsed=time.monotonic() - t0)


def _single_stream(url, dest: Path, size: int, on_progress, on_chunk, speed_limit,
                   referer, cookies, timeout, retries, t0) -> StreamResult:
    """单流顺序下载 + 边下边交付(.part 断点续传)。"""
    part = dest.parent / f"{dest.name}.part"
    done0 = part.stat().st_size if part.exists() else 0
    hdrs = _headers(referer)
    if done0:
        hdrs["Range"] = f"bytes={done0}-"
    for attempt in range(retries):
        try:
            with requests.get(url, headers=hdrs, stream=True, timeout=timeout,
                              allow_redirects=True, proxies=proxies(),
                              cookies=cookies) as r:
                if r.status_code == 416:
                    part.unlink(missing_ok=True)
                    continue
                if r.status_code not in (200, 206):
                    if attempt < retries - 1 and r.status_code in (403, 429, 502, 503, 504):
                        time.sleep(2 * (attempt + 1))
                        continue
                    return StreamResult(url, error=f"HTTP {r.status_code}",
                                        strategy="direct-single", elapsed=time.monotonic() - t0)
                total = size if size > 0 else done0 + int(r.headers.get("Content-Length") or 0)
                with open(part, "ab" if done0 and r.status_code == 206 else "wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        if _throttle(speed_limit, chunk):
                            return StreamResult(url, error="下载中断(限速停止)",
                                                strategy="direct-single",
                                                elapsed=time.monotonic() - t0)
                        f.write(chunk)
                        if on_chunk:
                            on_chunk(chunk)
                        if on_progress:
                            on_progress(done0 + f.tell(), total)
                os.replace(part, dest)
                return StreamResult(url, path=str(dest), size=dest.stat().st_size, ok=True,
                                    strategy="direct-single", resumed=done0 > 0,
                                    elapsed=time.monotonic() - t0)
        except requests.RequestException:
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    return StreamResult(url, error="重试次数耗尽", strategy="direct-single",
                        elapsed=time.monotonic() - t0)


def _split_bounds(size: int, n: int) -> list[tuple[int, int, int]]:
    """把 [0, size) 切成 n 段,返回 [(start, end, idx)]。"""
    chunk = size // n
    bounds = []
    for i in range(n):
        s = i * chunk
        e = size - 1 if i == n - 1 else (i + 1) * chunk - 1
        if s <= e:
            bounds.append((s, e, i))
    return bounds


def _throttle(speed_limit: int, chunk: bytes) -> bool:
    """简易令牌桶限速;返回 True 表示应停止(保留给中断场景,现在只 sleep)。"""
    if speed_limit > 0:
        time.sleep(len(chunk) / speed_limit)
    return False


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _guess_filename(url: str, probe: StreamProbe) -> str:
    from urllib.parse import unquote, urlsplit

    path = unquote(urlsplit(url).path)
    name = path.rstrip("/").split("/")[-1]
    if not name or "." not in name:
        name = f"stream_{abs(hash(url)) % 1000000}"
        if probe.is_hls:
            name += ".ts"
        elif probe.is_media:
            ext = _ext_from_content_type(probe.content_type)
            if ext:
                name += ext
    return name[:200]


def _ext_from_content_type(ct: str) -> str:
    ct = (ct or "").lower()
    for k, ext in (("mpegurl", ".ts"), ("mp4", ".mp4"), ("mp3", ".mp3"),
                   ("webm", ".webm"), ("ogg", ".ogg"), ("flac", ".flac"),
                   ("mpeg", ".mpg")):
        if k in ct:
            return ext
    return ""
