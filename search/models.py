"""资源检索 — 数据模型。

对齐计划 §4.2「发现层」:统一的 SearchResult 契约,引擎/聚合/筛选/探测
各层只认这个契约,与具体搜索引擎解耦。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

__all__ = [
    "SearchQuery",
    "SearchResult",
    "ProbeInfo",
    "EngineOutcome",
    "KIND_DIRECT_FILE",
    "KIND_WEBPAGE",
    "KIND_PAN_SHARE",
    "KIND_BLOCKED",
    "KIND_DEAD",
    "KIND_UNREACHABLE",
    "KIND_UNKNOWN",
]

# 可用性探测的结果类型(probe.kind)
KIND_DIRECT_FILE = "direct_file"      # 直链文件(Content-Type/扩展名判定)
KIND_WEBPAGE = "webpage"              # 普通网页(可能需要进页面再找资源)
KIND_PAN_SHARE = "pan_share"          # 网盘分享页(pan.baidu.com 等)
KIND_BLOCKED = "blocked"              # 反爬/验证码墙/限流
KIND_DEAD = "dead"                    # 404/410/明确失效
KIND_UNREACHABLE = "unreachable"      # 连接失败/超时/DNS
KIND_UNKNOWN = "unknown"              # 未探测


@dataclass
class SearchQuery:
    """一次检索请求。engines 为空 = 用全部可用引擎。"""

    text: str
    engines: list[str] = field(default_factory=list)
    per_engine: int = 10
    timeout: float = 12.0
    language: str = ""  # 预留:zh-CN / en 等


@dataclass
class ProbeInfo:
    """单个候选 URL 的可用性探测结果。"""

    status: int = 0
    content_type: str = ""
    size: int = -1
    kind: str = KIND_UNKNOWN
    final_url: str = ""
    note: str = ""


@dataclass
class SearchResult:
    """一个搜索结果(可跨引擎去重聚合)。"""

    title: str
    url: str
    snippet: str = ""
    engine: str = ""                    # 来源引擎(聚合后为首个命中引擎)
    engines: list[str] = field(default_factory=list)  # 被哪些引擎同时命中
    rank: int = 0                       # 在来源引擎内的排名
    score: float = 0.0                  # 聚合后的相关性得分
    normalized_url: str = ""
    dedup_key: str = ""
    probe: Optional[ProbeInfo] = None

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet[:300],
            "engine": self.engine,
            "engines": self.engines,
            "score": round(self.score, 2),
            "probe": (self.probe.__dict__ if self.probe else None),
        }


@dataclass
class EngineOutcome:
    """单个引擎一次检索的结果(含失败信息,供聚合层容错)。"""

    name: str
    results: list[SearchResult] = field(default_factory=list)
    error: str = ""
    elapsed: float = 0.0
