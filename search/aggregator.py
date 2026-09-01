"""检索聚合器 — 多引擎并发 → 合并去重 → 相关性排序 →(可选)可用性探测重排。

对齐 crawlee「uniqueKey 三层去重」与 firecrawl「引擎瀑布流 + Meta 上下文」:
- 引擎级容错:单个引擎失败/被拦不影响整体;连续失败自动熔断(见 engines 注册表);
- 结果级去重:规范化 URL 为 key,跨引擎合并(记录命中引擎列表,取最高排名);
- 探测重排:--probe 时对 top 候选并发探测,直链/网盘加分,失效/拦截降权。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from .engines import (
    EngineError, engine_order, get_engine, is_healthy, mark_failure, mark_success,
)
from .filter import filter_results, rank_results
from .models import EngineOutcome, ProbeInfo, SearchQuery, SearchResult
from .normalize import dedup_key, is_tracking_host
from .probe import apply_probe_weights, probe_many

__all__ = ["search", "search_engines"]

ENGINE_ORDER_LABEL = {"bing": "Bing", "baidu": "百度", "mojeek": "Mojeek",
                      "so360": "360", "duckduckgo": "DDG", "github": "GitHub"}

# 单引擎在最终列表的最大贡献:防止某个引擎(如 360 全量 so.com/link 中转链)
# 挤掉其它引擎的真实资源页。bias 参数透传给各引擎构造函数。
# 注:该值不能太小 —— 只有 1~2 家引擎可用(其余超时/403)时,4 条/引擎会把
# 总候选卡死在 8 条,真实源(电子书站/网盘页)排不进分析预算。8 条/引擎 + 中转链
# 扣分已足够防止单一引擎挤占。
_MAX_PER_ENGINE = 8


def _cap_per_engine(ranked: list, limit: int, max_per_engine: int = _MAX_PER_ENGINE) -> list:
    """按引擎配额截断:每个引擎最多贡献 max_per_engine 条,直到凑满 limit。"""
    if not limit:
        return ranked
    counts: dict[str, int] = {}
    out: list = []
    for r in ranked:
        eng = getattr(r, "engine", "") or ""
        if counts.get(eng, 0) >= max_per_engine:
            continue
        counts[eng] = counts.get(eng, 0) + 1
        out.append(r)
        if len(out) >= limit:
            break
    return out


def search_engines(query: str, engines: Optional[list[str]] = None,
                   per_engine: int = 10, timeout: float = 12.0) -> list[EngineOutcome]:
    """并发调多个引擎,返回每个引擎的独立结果(含错误)。"""
    names = engine_order(engines)
    outcomes: list[EngineOutcome] = []
    with ThreadPoolExecutor(max_workers=min(len(names), 6)) as pool:
        futs = {}
        for name in names:
            if not is_healthy(name):
                outcomes.append(EngineOutcome(name, error="熔断冷却中(连续失败)"))
                continue
            futs[pool.submit(_run_one, name, query, per_engine, timeout)] = name
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                outcomes.append(fut.result())
            except Exception as e:  # 兜底,不应发生
                mark_failure(name)
                outcomes.append(EngineOutcome(name, error=f"异常: {str(e)[:120]}"))
    return outcomes


def _run_one(name: str, query: str, per_engine: int, timeout: float) -> EngineOutcome:
    t0 = time.monotonic()
    try:
        eng = get_engine(name)
        results = eng.search(query, per_engine=per_engine)
        mark_success(name)
        return EngineOutcome(name=name, results=results, elapsed=time.monotonic() - t0)
    except EngineError as e:
        mark_failure(name)
        return EngineOutcome(name=name, error=str(e), elapsed=time.monotonic() - t0)
    except Exception as e:
        mark_failure(name)
        return EngineOutcome(name=name, error=f"{type(e).__name__}: {str(e)[:120]}",
                             elapsed=time.monotonic() - t0)


def _merge(results_by_engine: list[EngineOutcome]) -> list[SearchResult]:
    """跨引擎去重合并:规范化 URL 为 key,保留最靠前的排名,记录命中引擎。"""
    best: dict[str, SearchResult] = {}
    order: list[str] = []
    for oc in results_by_engine:
        for r in oc.results:
            key = dedup_key(r.url)
            if is_tracking_host(key):
                continue
            prev = best.get(key)
            if prev is None:
                r.dedup_key = key
                r.normalized_url = key
                r.url = key  # 统一用规范化后的 URL 作为候选
                r.engines = [r.engine]
                best[key] = r
                order.append(key)
            else:
                if r.engine not in prev.engines:
                    prev.engines.append(r.engine)
                if r.rank and (prev.rank == 0 or r.rank < prev.rank):
                    prev.rank = r.rank
                    prev.title = r.title
                    prev.snippet = r.snippet or prev.snippet
    return [best[k] for k in order]


def search(query: str, engines: Optional[list[str]] = None,
           per_engine: int = 10, probe: bool = False,
           limit: int = 20, timeout: float = 12.0,
           _outcomes: Optional[list[EngineOutcome]] = None) -> list[SearchResult]:
    """完整检索:聚合 → 去重 → 过滤 → 打分 →(可选)探测重排 → 截断。

    _outcomes 由调用方预先执行 search_engines 得到(避免重复请求引擎)。
    """
    if not query or not query.strip():
        return []

    if _outcomes is None:
        outcomes = search_engines(query, engines=engines, per_engine=per_engine, timeout=timeout)
    else:
        outcomes = _outcomes
    merged = _merge(outcomes)
    kept = filter_results(query, merged)
    ranked = rank_results(query, kept)

    if probe and ranked:
        top = ranked[: max(limit * 2, 10)]
        probes = probe_many([r.url for r in top])
        apply_probe_weights(top, probes)
        ranked.sort(key=lambda x: x.score, reverse=True)

    ranked = _cap_per_engine(ranked, limit)  # 单引擎配额,防一家挤占全部名额
    return ranked[:limit] if limit else ranked
