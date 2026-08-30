"""引擎注册表 — 有序、带健康状态(熔断)。

对齐 yt-dlp「注册表首匹配 + 专站优先/通用兜底」与 firecrawl「引擎瀑布流」:
- 注册顺序即默认优先级(本机实测可达性排序);
- 引擎连续失败 3 次标记不健康,冷却 5 分钟后自动恢复(聚合层跳过不健康引擎);
- 支持 - 按名取引擎 / 自定义顺序。
"""

from __future__ import annotations

import time
from typing import Optional

from .base import EngineError, SearchEngine
from .baidu import BaiduEngine
from .bing import BingEngine
from .duckduckgo import DuckDuckGoEngine
from .github import GitHubEngine
from .mojeek import MojeekEngine
from .so360 import So360Engine

__all__ = ["get_engine", "all_engine_names", "engine_order", "is_healthy", "mark_failure", "reset_health"]

# 注册顺序 = 默认优先级
_REGISTRY: dict[str, type[SearchEngine]] = {
    "bing": BingEngine,
    "baidu": BaiduEngine,
    "mojeek": MojeekEngine,
    "so360": So360Engine,
    "duckduckgo": DuckDuckGoEngine,
    "github": GitHubEngine,
}

# 健康状态: name -> {"fails": int, "until": float}
_HEALTH: dict[str, dict] = {}
COOLDOWN_SECONDS = 300.0
FAIL_THRESHOLD = 3

# 一次性构造好的实例(带默认配置)
_INSTANCES: dict[str, SearchEngine] = {}


def _inst(name: str) -> SearchEngine:
    if name not in _INSTANCES:
        _INSTANCES[name] = _REGISTRY[name]()
    return _INSTANCES[name]


def get_engine(name: str) -> SearchEngine:
    if name not in _REGISTRY:
        raise KeyError(f"未知引擎: {name}(可选: {', '.join(_REGISTRY)})")
    return _inst(name)


def all_engine_names() -> list[str]:
    return list(_REGISTRY)


def engine_order(names: Optional[list[str]] = None) -> list[str]:
    """解析引擎列表:None/空 = 全部;否则按给定顺序(未知项忽略)。"""
    if not names:
        return all_engine_names()
    out = []
    for n in names:
        n = n.strip().lower()
        if n in _REGISTRY and n not in out:
            out.append(n)
    return out


def is_healthy(name: str, now: Optional[float] = None) -> bool:
    h = _HEALTH.get(name)
    if not h:
        return True
    if h["fails"] < FAIL_THRESHOLD:
        return True
    return (now or time.monotonic()) >= h.get("until", 0)


def mark_failure(name: str) -> None:
    h = _HEALTH.setdefault(name, {"fails": 0, "until": 0.0})
    h["fails"] = h.get("fails", 0) + 1
    if h["fails"] >= FAIL_THRESHOLD:
        h["until"] = time.monotonic() + COOLDOWN_SECONDS


def mark_success(name: str) -> None:
    h = _HEALTH.setdefault(name, {"fails": 0, "until": 0.0})
    h["fails"] = 0


def reset_health() -> None:
    _HEALTH.clear()
