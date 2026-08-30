"""资源检索 — 核心入口。

用法(API):
    from search import search
    results = search("Python requests 教程 pdf", engines=["bing", "mojeek"], probe=True)
CLI:
    python -m search.cli "Python requests 教程 pdf" --probe --limit 15
"""

from .aggregator import search, search_engines
from .models import (
    KIND_BLOCKED, KIND_DEAD, KIND_DIRECT_FILE, KIND_PAN_SHARE,
    KIND_UNREACHABLE, KIND_UNKNOWN, KIND_WEBPAGE,
    EngineOutcome, ProbeInfo, SearchQuery, SearchResult,
)

__all__ = [
    "search",
    "search_engines",
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
