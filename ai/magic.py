"""魔法字节嗅探 — 纯规则、零成本、可单测。

下载后裁决格式的第一步(规则做工具):按文件头判断真实类型,
不依赖扩展名(很多下载 URL 无扩展名或扩展名是假的)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

__all__ = ["sniff_file", "MAGIC_TABLE"]

# (魔数, 类型标签, 说明)
MAGIC_TABLE: list[tuple[bytes, str, str]] = [
    (b"\x89PNG\r\n\x1a\n", "png", "PNG 图片"),
    (b"\xff\xd8\xff", "jpg", "JPEG 图片"),
    (b"GIF87a", "gif", "GIF 图片"),
    (b"GIF89a", "gif", "GIF 图片"),
    (b"RIFF", "riff", "RIFF 容器(需确认 WAVE/WEBP 标记)"),
    (b"%PDF-", "pdf", "PDF 文档"),
    (b"PK\x03\x04", "zip", "ZIP 压缩包(含 epub/jar/litematic/docx 外衣)"),
    (b"PK\x05\x06", "zip", "ZIP 空包"),
    (b"\x1f\x8b\x08", "gzip", "gzip 压缩(常见于 NBT:.litematic/.schematic)"),
    (b"7z\xbc\xaf\x27\x1c", "7z", "7z 压缩包"),
    (b"Rar!\x1a\x07", "rar", "RAR 压缩包"),
    (b"\x1a\x45\xdf\xa3", "mkv", "Matroska/WebM 视频"),
    (b"ftyp", "mp4", "MP4/M4A 媒体(box 格式)"),
    (b"ID3", "mp3", "MP3 音频"),
    (b"OggS", "ogg", "Ogg 音频/视频"),
    (b"fLaC", "flac", "FLAC 无损音频"),
    (b"BOOKMOBI", "mobi", "Mobi/AZW3 电子书"),
    (b"AT&TFORM", "djvu", "DJVU 电子书"),
    (b"\x0a", "nbt", "未压缩 NBT(TAG_Compound 头,.schematic 可能)"),
    (b"\x50\x4b\x03", "zip", "ZIP(宽松魔数)"),
]


def sniff_file(path: str | Path, sample: int = 1024) -> Optional[dict]:
    """读取文件头,返回 {magic, kind, note};无法识别返回 None。"""
    p = Path(path)
    if not p.exists():
        return None
    try:
        with open(p, "rb") as f:
            head = f.read(sample)
    except Exception:
        return None
    return sniff_bytes(head)


def sniff_bytes(head: bytes) -> Optional[dict]:
    """对文件头字节做魔数匹配(含 RIFF→WebP/WAV、gzip 特判)。"""
    for magic, kind, note in MAGIC_TABLE:
        if head.startswith(magic):
            # RIFF 特判:同一容器区分 WebP(WEBP 标记)与 WAV(WAVE 标记)
            if kind == "riff":
                if len(head) >= 12 and head[8:12] == b"WEBP":
                    return {"magic": magic.hex(), "kind": "webp", "note": "WebP 图片"}
                if len(head) >= 12 and head[8:12] == b"WAVE":
                    return {"magic": magic.hex(), "kind": "wav", "note": "WAV 音频"}
                continue
            return {"magic": magic.hex(), "kind": kind, "note": note}
    return None
