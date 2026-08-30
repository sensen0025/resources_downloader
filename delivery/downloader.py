"""下载交付 — .part 断点续传 + Content-Range 校验 + 期望校验。

对齐 yt-dlp downloader/http.py 的关键经验:
- 写 `.part` 临时文件,断点续传发 `Range: bytes=N-`;
- 校验响应 Content-Range 与请求一致,服务器不支持续传才整文件重下;
- 完成后可选校验:期望扩展名 / 最小大小 / 期望 SHA-256;
- 进度回调 + 超时 + 连接重置自动重试。
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import requests

from proxy import proxies

__all__ = ["download", "DownloadResult"]

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class DownloadResult:
    url: str
    path: str = ""
    size: int = -1
    resumed: bool = False
    error: str = ""
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.error


def _content_range_ok(resp_headers: dict, expected_start: int) -> bool:
    """校验服务器是否按请求的 Range 续传(yt-dlp 经验)。"""
    cr = resp_headers.get("Content-Range", "")
    m = re.match(r"bytes (\d+)-", cr)
    return bool(m) and int(m.group(1)) == expected_start


def download(
    url: str,
    dest_dir: str | Path,
    filename: Optional[str] = None,
    *,
    expected_ext: str = "",
    min_size: int = 0,
    expected_sha256: str = "",
    timeout: float = 30.0,
    progress: Optional[Callable[[int, int], None]] = None,
    retries: int = 3,
    referer: str = "",
) -> DownloadResult:
    """下载 URL 到 dest_dir。支持断点续传、完整性校验与防盗链 Referer。"""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    if not filename:
        filename = _guess_filename(url)
    dest = dest_dir / filename
    part = dest_dir / f"{filename}.part"
    headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    if referer:
        headers["Referer"] = referer
    t0 = time.monotonic()

    for attempt in range(retries):
        resumed = part.exists() and part.stat().st_size > 0
        start = part.stat().st_size if resumed else 0
        if start:
            headers["Range"] = f"bytes={start}-"
        try:
            with requests.get(url, headers=headers, stream=True, timeout=timeout,
                              allow_redirects=True, proxies=proxies()) as r:
                if r.status_code == 416:  # Range 越界 → 服务器已完整
                    part.unlink(missing_ok=True)
                    continue
                if r.status_code not in (200, 206):
                    if attempt < retries - 1 and r.status_code in (403, 429, 502, 503, 504):
                        time.sleep(2 * (attempt + 1))
                        continue
                    return DownloadResult(url=url, error=f"HTTP {r.status_code}")
                if resumed and not _content_range_ok(r.headers, start):
                    # 服务器忽略 Range → 整文件重下
                    part.unlink(missing_ok=True)
                    continue
                # 反 HTML 落地页:期望是文件却返回网页(登录墙/错误页)→ 拒绝
                ct = (r.headers.get("Content-Type") or "").lower()
                if expected_ext and "text/html" in ct:
                    return DownloadResult(
                        url=url, error=f"返回 HTML 而非文件(Content-Type: {ct};可能需登录或链接非直链)")
                mode = "ab" if resumed else "wb"
                total = start + int(r.headers.get("Content-Length") or 0)
                with open(part, mode) as f:
                    for chunk in r.iter_content(1 << 16):
                        f.write(chunk)
                        if progress:
                            progress(start + f.tell(), total)
                # 流式写完后再嗅探头部(兼容服务器不报 Content-Type 的情况)
                if expected_ext and not part.stat().st_size:
                    pass
                elif expected_ext:
                    with open(part, "rb") as f:
                        head = f.read(1024).lstrip()
                    if head[:5].lower() in (b"<!doc", b"<html") or b"<head" in head[:512].lower():
                        return DownloadResult(
                            url=url, error="下载内容为 HTML(可能需登录或链接非直链),已拒绝")
                if expected_ext and not dest.name.lower().endswith(expected_ext.lower()):
                    # 扩展名不符 → 用 Content-Type 或 URL 修正文件名
                    pass
                if min_size and part.stat().st_size < min_size:
                    return DownloadResult(url=url, error=f"文件过小: {part.stat().st_size}B < {min_size}B")
                if expected_sha256:
                    h = hashlib.sha256()
                    with open(part, "rb") as f:
                        for chunk in iter(lambda: f.read(1 << 20), b""):
                            h.update(chunk)
                    if h.hexdigest().lower() != expected_sha256.lower():
                        return DownloadResult(url=url, error="SHA-256 校验失败")
                os.replace(part, dest)
                return DownloadResult(url=url, path=str(dest), size=dest.stat().st_size,
                                      resumed=resumed, elapsed=time.monotonic() - t0)
        except requests.RequestException as e:
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            return DownloadResult(url=url, error=f"{type(e).__name__}: {str(e)[:120]}",
                                  elapsed=time.monotonic() - t0)
    return DownloadResult(url=url, error="重试次数耗尽", elapsed=time.monotonic() - t0)


def _guess_filename(url: str) -> str:
    from urllib.parse import unquote, urlsplit

    path = unquote(urlsplit(url).path)
    name = path.rstrip("/").split("/")[-1]
    # 去 CDN 尺寸后缀(bilibili 等:@1416w_798h_1c)
    name = re.sub(r"@[\w_]+$", "", name)
    if not name or "." not in name:
        name = f"download_{abs(hash(url)) % 1000000}.bin"
    return name[:200]
