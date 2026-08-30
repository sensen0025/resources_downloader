"""流式下载技能 — 专治音视频等大文件。

    from skills.streaming import probe_stream, stream_download, StreamResult

底层引擎(纯规则能力)在这里;AI 决策型工具 `download_stream` 在 `ai/skills.py`
注册(import ai 时自动进 AgentCore 目录),media 类型下载在
`agent/tasks/fetch_resource.py` 自动分流到本技能。
"""

from .hls import HlsPlaylist, hls_download, parse_m3u8
from .streamer import (
    MEDIA_EXTS,
    StreamProbe,
    StreamResult,
    is_media_url,
    probe_stream,
    stream_download,
)

__all__ = [
    "probe_stream",
    "stream_download",
    "hls_download",
    "parse_m3u8",
    "HlsPlaylist",
    "StreamProbe",
    "StreamResult",
    "is_media_url",
    "MEDIA_EXTS",
]
__version__ = "0.1.0"
