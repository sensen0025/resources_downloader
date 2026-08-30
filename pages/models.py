"""页面分析层 — 数据模型。

对齐计划 v4 §3.1:页面分级 + 资源链接提取的统一契约。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

__all__ = [
    "PageClass",
    "PageInfo",
    "ExtractedResource",
    "PageAnalysis",
]


class PageClass(str, Enum):
    """页面分级结果(决定走哪条获取路径)。"""

    DIRECT_FILE = "direct_file"        # 本身就是资源直链
    DOWNLOAD_PAGE = "download_page"    # 页面内有关键下载链接
    PAN_SHARE = "pan_share"            # 网盘分享页
    LOGIN_REQUIRED = "login_required"  # 需登录/验证码(资源在登录墙后)
    AGGREGATOR = "aggregator"          # 列表/索引页(提取子链接继续)
    BLOCKED = "blocked"                # 反爬/验证码墙
    DEAD = "dead"                      # 失效
    UNKNOWN = "unknown"                # 无法判定(可交 LLM 复核)


@dataclass
class PageInfo:
    """一次页面抓取的结果。"""

    url: str
    status: int = 0
    final_url: str = ""
    content_type: str = ""
    title: str = ""
    html: str = ""
    size: int = -1
    error: str = ""
    fetched_at: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.error and self.status in (200, 206)


@dataclass
class ExtractedResource:
    """从页面里提取出的一个资源候选(下载链接/网盘/嵌入)。"""

    url: str                      # 规范化后的资源 URL
    kind: str = "link"            # direct_file / pan_share / download_button / iframe / link
    name: str = ""                # 资产语义名称(alt/title/卡片标题/模型名称)
    file_ext: str = ""
    text: str = ""                # 锚文本/上下文
    size_hint: int = -1
    score: float = 0.0            # 提取器给的置信(排序用)

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "name": (self.name or self.text or "")[:100],
            "kind": self.kind,
            "ext": self.file_ext,
            "score": self.score,
        }


@dataclass
class PageAnalysis:
    """对单个页面的完整分析结果。"""

    page_url: str
    page_class: PageClass = PageClass.UNKNOWN
    title: str = ""
    description: str = ""
    author: str = ""
    meta: dict = field(default_factory=dict)          # OG/JSON-LD 等原始元数据
    resources: list[ExtractedResource] = field(default_factory=list)
    needs_login: bool = False
    reason: str = ""
    llm_verified: bool = False

    @property
    def best_resources(self) -> list[ExtractedResource]:
        """按分数降序的资源列表。"""
        return sorted(self.resources, key=lambda r: r.score, reverse=True)

    def to_dict(self) -> dict:
        return {
            "page_url": self.page_url,
            "page_class": self.page_class.value,
            "title": self.title,
            "description": (self.description or "")[:200],
            "author": self.author,
            "needs_login": self.needs_login,
            "reason": self.reason,
            "llm_verified": self.llm_verified,
            "resources": [r.to_dict() for r in self.best_resources[:20]],
        }
