"""万能下载技能 — 直链 / m3u8 切片流 / Cloudflare 反制 / 播放页自动找流。

    from skills.universal import universal_download, resolve_stream

底层引擎在这里;AI 工具 `universal_download` 在 `ai/skills.py` 注册(AgentCore 目录),
`agent/tools.py` 的 download 工具对 m3u8/CF 自动路由到本技能。
"""

from .cf import browser_cookies, is_cf_challenge
from .ffmpeg_tool import find_ffmpeg, merge_hls_ffmpeg
from .universal import (
    download_page_stream,
    is_page_url,
    resolve_stream,
    universal_download,
)

__all__ = [
    "universal_download",
    "resolve_stream",
    "download_page_stream",
    "is_page_url",
    "is_cf_challenge",
    "browser_cookies",
    "find_ffmpeg",
    "merge_hls_ffmpeg",
]
__version__ = "0.1.0"
