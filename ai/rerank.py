"""AI 语义重排 — 一次批量 LLM 调用给候选打相关分(替代 term-in-text 硬过滤)。

词法只做宽松保底预筛(不误杀),真正的相关性判断交给 AI:
top-N 候选压缩成紧凑 JSON,一次调用返回 每条分数 + 保留/丢弃 + 理由。
LLM 不可用时自动退化为词法排序(现状),永不阻塞主流程。
"""

from __future__ import annotations

import json
from typing import Optional

from search.models import SearchResult

__all__ = ["rerank_candidates"]


def rerank_candidates(
    query: str,
    candidates: list[SearchResult],
    llm=None,
    keep: int = 8,
) -> list[SearchResult]:
    """批量语义重排:返回按 AI 相关性分排序的候选。llm=None 时返回原样(词法序)。"""
    if not candidates:
        return []
    if llm is None:
        try:
            from agent.llm import LLMClient

            llm = LLMClient()
        except Exception:
            return candidates  # 无 LLM → 保持词法排序

    top = candidates[: keep * 3]
    payload = {
        "query": query,
        "candidates": [
            {"i": idx, "title": (c.title or "")[:80], "url": c.url[:100],
             "snippet": (c.snippet or "")[:100]}
            for idx, c in enumerate(top)
        ],
    }
    prompt = (
        "你是资源检索相关性裁判。用户想获取的资源意图:\n"
        f"{query}\n\n"
        "以下是候选(标题/URL/摘要)。判断每个候选与意图的相关性:\n"
        "0 = 完全无关(广告/百科/导航/代码仓库等),1-3 = 弱相关,4-6 = 相关,7-10 = 高度相关(很可能就是资源本身或其直接页面)。\n"
        "只输出 JSON 数组,每项 {\"i\": 下标, \"score\": 0-10, \"keep\": true/false, \"reason\": \"一句话\"}。\n"
        "候选:\n" + json.dumps(payload["candidates"], ensure_ascii=False)
    )
    try:
        data = llm.chat_json([{"role": "user", "content": prompt}], max_tokens=1200)
        items = data if isinstance(data, list) else data.get("items", data.get("results", []))
        scores = {int(it["i"]): it for it in items if isinstance(it, dict) and "i" in it}
    except Exception:
        return candidates

    scored: list[tuple[float, SearchResult]] = []
    for idx, c in enumerate(top):
        it = scores.get(idx)
        if it and it.get("keep", True):
            score = float(it.get("score", 0))
            c.score = (c.score or 0) + score * 1.0  # 叠加 AI 分
            c.snippet = c.snippet or it.get("reason", "")
            scored.append((score, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:keep]]
