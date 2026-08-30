"""HLS(m3u8) 下载 — 解析播放列表 → 分段并发下载 → 按序合并。

对齐 yt-dlp hls 经验:
- master playlist(EXT-X-STREAM-INF)→ 自动选带宽最高的 variant;
- media playlist(EXTINF)→ 分段 URL(urljoin 解析相对地址);
- AES-128 加密分段(EXT-X-KEY METHOD=AES-128,URI,IV)→ 用 cryptography 解密,
  IV 缺省 = 分段序号(16 字节大端),与 HLS 规范一致;
- fmp4(EXT-X-MAP)→ 无法用纯拼接合并,诚实报错提示 ffmpeg 方案;
- 分段并发上限 8,失败重试,进度回调(on_progress/on_chunk)。
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urljoin, urlsplit

import requests

from proxy import proxies

from .streamer import StreamResult, _headers

__all__ = ["hls_download", "parse_m3u8", "HlsPlaylist"]

MAX_CONCURRENT = 8


class HlsPlaylist:
    """解析后的播放列表。"""

    def __init__(self) -> None:
        self.is_master = False
        self.variants: list[dict] = []      # [{uri, bandwidth}]
        self.segments: list[dict] = []      # [{uri, duration, iv, key_uri}]
        self.key_uri: str = ""
        self.key_iv: Optional[bytes] = None
        self.has_map = False                # fmp4(EXT-X-MAP)→ 需 ffmpeg
        self.encrypted = False


def parse_m3u8(text: str, base_url: str) -> HlsPlaylist:
    """解析 m3u8 文本。base_url 用于把相对分段地址变绝对。"""
    pl = HlsPlaylist()
    lines = [ln.strip() for ln in text.splitlines()]
    if not lines or "#EXTM3U" not in lines[0]:
        raise ValueError("不是合法的 m3u8(缺 #EXTM3U)")

    current_key_uri = ""
    current_key_iv: Optional[bytes] = None
    seg_index = 0

    for ln in lines:
        if ln.startswith("#EXT-X-STREAM-INF"):
            pl.is_master = True
            m = re.search(r"BANDWIDTH=(\d+)", ln)
            bw = int(m.group(1)) if m else 0
            pl.variants.append({"uri": "", "bandwidth": bw})
        elif ln.startswith("#EXT-X-KEY"):
            pl.encrypted = True
            m = re.search(r'METHOD=([^,]+)', ln)
            method = m.group(1) if m else ""
            if method.upper() != "AES-128":
                raise ValueError(f"不支持的加密方式 {method}(仅 AES-128)")
            u = re.search(r'URI="([^"]+)"', ln)
            if u:
                current_key_uri = urljoin(base_url, u.group(1))
            iv = re.search(r'IV=0x([0-9a-fA-F]+)', ln)
            current_key_iv = bytes.fromhex(iv.group(1)) if iv else None
        elif ln.startswith("#EXT-X-MAP"):
            pl.has_map = True
        elif ln.startswith("#EXTINF"):
            m = re.search(r":([\d.]+)", ln)
            duration = float(m.group(1)) if m else 0.0
            pl.segments.append({"uri": "", "duration": duration,
                                "iv": current_key_iv, "key_uri": current_key_uri,
                                "index": seg_index})
            seg_index += 1
        elif ln.startswith("#"):
            continue
        elif ln.strip():
            # 普通行 = 资源地址(紧跟 EXTINF 的分段,或紧跟 STREAM-INF 的 variant)
            if pl.is_master and pl.variants and not pl.variants[-1]["uri"]:
                pl.variants[-1]["uri"] = urljoin(base_url, ln)
            elif pl.segments and not pl.segments[-1]["uri"]:
                pl.segments[-1]["uri"] = urljoin(base_url, ln)

    # master 里 EXTINF 是无意义的,清掉误收集
    if pl.is_master:
        pl.segments = []
    return pl


def hls_download(url: str, dest: Path, *,
                 on_progress: Optional[Callable[[int, int], None]] = None,
                 on_chunk: Optional[Callable[[bytes], None]] = None,
                 referer: str = "", cookies: Optional[dict] = None,
                 timeout: float = 30.0, retries: int = 3) -> StreamResult:
    """下载 HLS 流并合并为单文件。dest 为最终输出路径。cookies 供 CF 会话复用。"""
    text = _fetch_text(url, referer, cookies, timeout)
    if not text:
        return StreamResult(url, error="获取 m3u8 失败")

    pl = parse_m3u8(text, url)
    if pl.is_master:
        if not pl.variants:
            return StreamResult(url, error="master 播放列表无 variant")
        best = max(pl.variants, key=lambda v: v["bandwidth"])
        variant_url = best["uri"]
        text = _fetch_text(variant_url, referer, cookies, timeout)
        if not text:
            return StreamResult(url, error="获取 variant 播放列表失败")
        pl = parse_m3u8(text, variant_url)

    if not pl.segments:
        return StreamResult(url, error="播放列表无分段")
    if pl.has_map:
        return StreamResult(url, error="fmp4(HLS 分片 mp4)需要 ffmpeg 合并,暂不支持纯拼接")

    key_bytes = None
    if pl.encrypted:
        key_bytes = _fetch_key(pl.key_uri or pl.segments[0]["key_uri"] or "",
                               referer, cookies, timeout)
        if key_bytes is None:
            return StreamResult(url, error="获取 AES-128 密钥失败(需 cryptography)")

    # 并发下载分段到临时目录
    with tempfile.TemporaryDirectory(prefix="rh_hls_") as td:
        tmp = Path(td)
        total = len(pl.segments)
        done = 0

        def fetch_one(seg: dict) -> Optional[Path]:
            part = tmp / f"{seg['index']:06d}.bin"
            if not _fetch_segment(seg["uri"], part, key_bytes, seg["iv"], seg["index"],
                                  referer, cookies, timeout, retries):
                return None
            return part

        results: list[Optional[Path]] = [None] * total
        with ThreadPoolExecutor(max_workers=min(MAX_CONCURRENT, total)) as pool:
            futures = {pool.submit(fetch_one, s): s["index"] for s in pl.segments}
            for fut in as_completed(futures):
                idx = futures[fut]
                results[idx] = fut.result()
                done += 1
                if on_progress:
                    on_progress(done, total)

        if any(p is None for p in results):
            return StreamResult(url, error="部分分段下载失败(重试耗尽)", strategy="hls")

        # 按序合并 + 边下边交付
        dest.parent.mkdir(parents=True, exist_ok=True)
        final_part = dest.parent / f"{dest.name}.part"
        with open(final_part, "wb") as out:
            for p in results:
                with open(p, "rb") as f:
                    while True:
                        chunk = f.read(1 << 16)
                        if not chunk:
                            break
                        out.write(chunk)
                        if on_chunk:
                            on_chunk(chunk)
        os.replace(final_part, dest)

    return StreamResult(url, path=str(dest), size=dest.stat().st_size, ok=True,
                        strategy="hls", segments=total, elapsed=0.0)


# ---------------------------------------------------------------- 内部

def _fetch_text(url: str, referer: str, cookies: Optional[dict],
                timeout: float) -> str:
    try:
        with requests.get(url, headers=_headers(referer), timeout=timeout,
                          allow_redirects=True, proxies=proxies(),
                          cookies=cookies) as r:
            if r.status_code != 200:
                return ""
            return r.text
    except requests.RequestException:
        return ""


def _fetch_key(key_uri: str, referer: str, cookies: Optional[dict],
               timeout: float) -> Optional[bytes]:
    if not key_uri:
        return None
    try:
        with requests.get(key_uri, headers=_headers(referer), timeout=timeout,
                          allow_redirects=True, proxies=proxies(),
                          cookies=cookies) as r:
            if r.status_code != 200:
                return None
            return r.content
    except requests.RequestException:
        return None


def _fetch_segment(uri: str, dest: Path, key_bytes: Optional[bytes],
                   iv: Optional[bytes], seg_index: int,
                   referer: str, cookies: Optional[dict],
                   timeout: float, retries: int) -> bool:
    raw = None
    for attempt in range(retries):
        try:
            with requests.get(uri, headers=_headers(referer), timeout=timeout,
                              allow_redirects=True, proxies=proxies(),
                              cookies=cookies) as r:
                if r.status_code != 200:
                    if attempt < retries - 1:
                        time.sleep(1 * (attempt + 1))
                    continue
                raw = r.content
                break
        except requests.RequestException:
            if attempt < retries - 1:
                time.sleep(1 * (attempt + 1))
    if raw is None:
        return False
    if key_bytes is not None:
        raw = _decrypt_aes128(raw, key_bytes, iv, seg_index)
        if raw is None:
            return False
    dest.write_bytes(raw)
    return True


def _decrypt_aes128(data: bytes, key: bytes, iv: Optional[bytes], seg_index: int) -> Optional[bytes]:
    """AES-128-CBC 解密 HLS 分段(IV 缺省 = 分段序号 16 字节大端)。"""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        return None
    if len(key) not in (16, 32):
        return None
    iv_bytes = iv if iv else struct.pack(">Q", seg_index).rjust(16, b"\x00")
    try:
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv_bytes))
        dec = cipher.decryptor()
        out = dec.update(data) + dec.finalize()
    except Exception:
        return None
    # PKCS#7 去填充
    if out:
        pad = out[-1]
        if 1 <= pad <= 16:
            out = out[:-pad]
    return out
