"""ffmpeg 定位与 HLS 合并兜底 — fmp4/重封装场景用 ffmpeg 拉流合并。

ts 明文切片走 skills/streaming 自带合并(零依赖、快);以下情况交给 ffmpeg:
- fmp4(#EXT-X-MAP)分片,纯拼接会损坏;
- 自带合并失败的复杂流(加密变体/不连续 PTS 等)。

ffmpeg 未安装时返回明确提示(apt install ffmpeg / winget install ffmpeg)。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

__all__ = ["find_ffmpeg", "merge_hls_ffmpeg"]

_COMMON_PATHS = (
    "/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    r"C:\tools\ffmpeg\bin\ffmpeg.exe",
    os.path.expanduser(r"~\scoop\apps\ffmpeg\current\bin\ffmpeg.exe"),
)


def find_ffmpeg() -> Optional[str]:
    """定位 ffmpeg:PATH 查找 + 常见安装路径。"""
    found = shutil.which("ffmpeg")
    if found:
        return found
    for cand in _COMMON_PATHS:
        if Path(cand).exists():
            return cand
    return None


def merge_hls_ffmpeg(m3u8_url: str, dest: str | Path, *,
                     referer: str = "", cookies: Optional[dict] = None,
                     timeout: float = 1800.0) -> tuple[bool, str]:
    """ffmpeg 拉 m3u8 流合并为单文件。返回 (ok, error_or_path)。"""
    ff = find_ffmpeg()
    if not ff:
        return False, "ffmpeg 未安装(ts 切片可走自带合并;fmp4 需 ffmpeg: apt install ffmpeg / winget install ffmpeg)"
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # ffmpeg -headers 作用于所有 http 请求(传 Referer / Cookie 防盗链)
    headers = []
    if referer:
        headers.append(f"Referer: {referer}\r\n")
    if cookies:
        hdr = "; ".join(f"{k}={v}" for k, v in cookies.items())
        headers.append(f"Cookie: {hdr}\r\n")
    cmd = [ff, "-y", "-loglevel", "error", "-http_persistent", "0"]
    for h in headers:
        cmd += ["-headers", h]
    cmd += ["-i", m3u8_url, "-c", "copy", "-bsf:a", "aac_adtstoasc", str(dest)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"ffmpeg 超时(>{timeout}s)"
    except Exception as e:
        return False, f"ffmpeg 执行失败: {type(e).__name__}: {str(e)[:120]}"
    if proc.returncode != 0:
        return False, f"ffmpeg 退出码 {proc.returncode}: {(proc.stderr or proc.stdout or '')[:300]}"
    if not dest.exists() or dest.stat().st_size == 0:
        return False, "ffmpeg 输出为空文件"
    return True, str(dest)
