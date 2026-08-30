"""交付层 — 下载/校验(计划 v4 §delivery)。"""

from .downloader import DownloadResult, download

__all__ = ["download", "DownloadResult"]
