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
    timeout: float = 120.0,
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
    current_url = url  # TLS 握手失败时回退 http(部分站 https 配置损坏,只提供 http)

    for attempt in range(retries):
        resumed = part.exists() and part.stat().st_size > 0
        start = part.stat().st_size if resumed else 0
        if start:
            headers["Range"] = f"bytes={start}-"
        try:
            with requests.get(current_url, headers=headers, stream=True, timeout=timeout,
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
                        # 墙钟超时:requests 的 timeout 只管单次 socket 读写,
                        # 慢速但不断流的服务器(如 92wx.la ~1KB/s)会让任务挂死数十分钟。
                        # 这里按总耗时硬性截断,保留 .part 断点(下次可续传)。
                        if time.monotonic() - t0 > timeout:
                            return DownloadResult(
                                url=url,
                                error=f"下载超时(>{timeout:g}s 墙钟),已保留 .part 断点",
                                elapsed=time.monotonic() - t0)
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
                    # 扩展名不符 → 用 Content-Type / 内容嗅探修正文件名
                    # (乐书谷类站点下载链无扩展名:down.leshugu.info/down/207942 → text/plain)
                    new_name = _fix_filename(dest.name, ct, part)
                    if new_name:
                        new_part = dest_dir / f"{new_name}.part"
                        os.replace(part, new_part)
                        part = new_part
                        dest = dest_dir / new_name
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
            # TLS 握手失败(SSLError)→ 若还是 https,立即回退 http 重试同一轮
            # (部分站点如 80ge.info 的 https 配置损坏/被墙,只提供 http 服务)
            if (isinstance(e, requests.exceptions.SSLError)
                    and current_url.startswith("https://")):
                current_url = "http://" + current_url[len("https://"):]
                continue
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


# Content-Type → 扩展名(无扩展名下载链补名用)
_CT_EXT = {
    "text/plain": ".txt",
    "text/html": ".html",
    "application/zip": ".zip",
    "application/x-zip-compressed": ".zip",
    "application/x-rar-compressed": ".rar",
    "application/vnd.rar": ".rar",
    "application/pdf": ".pdf",
    "application/epub+zip": ".epub",
    "application/x-mobipocket-ebook": ".mobi",
    "application/octet-stream": ".bin",
}


def _fix_filename(name: str, content_type: str, part: Path) -> str:
    """给无真实扩展名的文件补扩展名:优先 Content-Type,缺失时嗅探内容。

    _guess_filename 对无扩展名 URL 落成 download_XXX.bin(占位),
    这里按实际类型修正为 .txt/.zip/... → 下载探针才能按期望扩展名计数。
    返回新文件名;无需修正返回空串。
    """
    import mimetypes

    suffix = Path(name).suffix.lower()
    if suffix not in ("", ".bin"):  # 已有真实扩展名(或占位 .bin 之外的)不动
        return ""
    ct = (content_type or "").lower().split(";")[0].strip()
    ext = _CT_EXT.get(ct, "")
    if not ext:
        if ct.startswith(("audio/", "video/", "image/")):
            ext = mimetypes.guess_extension(ct) or ".bin"
        elif ct.startswith("text/"):
            ext = ".txt"
    if not ext and part.exists() and part.stat().st_size > 0:
        # 内容嗅探:前 4KB 可打印/多字节文本占比高 → txt
        try:
            head = part.read_bytes()[:4096]
            printable = sum(1 for b in head if 9 <= b <= 13 or 32 <= b < 127 or b >= 0x80)
            if head and printable / len(head) > 0.9:
                ext = ".txt"
        except OSError:
            pass
    if ext:
        return Path(name).stem + ext if suffix == ".bin" else Path(name).name + ext
    return ""
