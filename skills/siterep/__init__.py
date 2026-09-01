"""站点信誉技能 — LLM 给访问过的网站打分(0-9)+ 短描述,录入信誉库;
下次任务按向量相似度查 top-k(带评分),优选好站、避开差站。

两个工具:
- record_site_feedback: 批量录入本次任务访问过的站点(LLM 批量打分,失败回退启发式);
- site_lookup        : 按查询查信誉库 top-k(向量相似度 + 评分),AI 选站/Agent 导航用。
"""

from __future__ import annotations

import json
import re
from typing import Optional

from skills.core import ToolResult, tool

from . import store

__all__ = [
    "record_feedback", "score_sites_with_llm", "heuristic_score",
    "record_site_feedback", "site_lookup",
]

_MAX_BATCH = 15  # 单次 LLM 打分的站点上限(防 prompt 过大/费用失控)
_MAX_DESC = 60   # 短描述长度上限


# ---------------------------------------------------------------- LLM 批量打分

_SCORE_PROMPT = """你是站点信誉评估器。下面是本次资源获取任务访问过的网站与观察到的信号。
对每个网站输出 0-9 的综合评分和一句不超过 40 字的中文短描述。
评分依据:
- 内容获取容易程度:有直链/可直接下载/章节可爬 = 高;层层跳转/需登录/需付费 = 低;
- 是否存在虚假/误导:"下载"按钮其实是书页、返回 HTML 冒充文件、SEO 引导迷宫 = 低;
- 可访问性:超时/无法连接/被拦截 = 低;
- 实际结果:信号中 downloaded=true 或 agent_ok=true 或 merge_ok=true(实际拿到了文件)
  必须给 ≥7 分,描述要体现"可下载/已成功获取";信号里 downloaded=false 且 agent_ok=false
  但页面有下载入口 = 中等(4-5);完全没拿到内容 = 低。
只输出一个 JSON 数组,不要输出任何其他文字,格式:
[{"host":"example.com","score":7,"description":"短描述"}]"""


def _extract_json_array(text: str) -> list:
    m = re.search(r"\[.*\]", text or "", re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def heuristic_score(site: dict) -> int:
    """LLM 不可用时的启发式评分(基于本次观察信号,0-9)。"""
    s = site.get("outcome") or ""
    pc = (site.get("page_class") or "").lower()
    if site.get("downloaded") or s in ("downloaded", "agent_ok"):
        return 8
    if s in ("merge_ok",):
        return 7
    if site.get("fake") or s in ("fake", "html") or "html" in (site.get("note") or "").lower():
        return 1
    if s in ("blocked",) or "blocked" in pc:
        return 1
    if s in ("login_wall", "paid") or "login" in pc:
        return 2
    if s in ("slow", "timeout"):
        return 3
    if pc in ("aggregator", "unknown", "download_page"):
        return 4
    return 4


def _heuristic_description(site: dict) -> str:
    pc = site.get("page_class") or ""
    reason = site.get("reason") or ""
    return (f"{pc}页" + (f":{reason}" if reason else ""))[:_MAX_DESC] or site.get("host", "")


def score_sites_with_llm(sites: list[dict]) -> dict[str, tuple[Optional[int], str]]:
    """对站点批量 LLM 打分 → {host: (score, description)};失败/缺项回退启发式。

    永不抛异常:LLM 不可用或解析失败时每个站点都得到启发式评分。
    """
    out: dict[str, tuple[Optional[int], str]] = {}
    try:
        from agent.llm import LLMClient

        lines = []
        for i, s in enumerate(sites, 1):
            parts = [f"- {i}. host: {s.get('host')}",
                     f"   url: {s.get('url')}",
                     f"   page_class: {s.get('page_class')}",
                     f"   reason: {s.get('reason')}"]
            for k in ("downloaded", "agent_ok", "merge_ok", "fake", "skipped"):
                if s.get(k) is not None:
                    parts.append(f"   {k}: {s[k]}")
            if s.get("note"):
                parts.append(f"   note: {s['note']}")
            lines.append("\n".join(parts))
        reply = LLMClient(timeout=60).chat(
            [{"role": "system", "content": _SCORE_PROMPT},
             {"role": "user", "content": "访问过的网站:\n" + "\n".join(lines)}],
            temperature=0.2, max_tokens=2048,
        )
        for item in _extract_json_array(reply):
            host = str(item.get("host") or "").strip().lower()
            if not host:
                continue
            try:
                score = max(0, min(9, int(item.get("score"))))
            except (TypeError, ValueError):
                score = None
            desc = str(item.get("description") or "")[:_MAX_DESC]
            out[host] = (score, desc)
    except Exception:
        pass  # LLM 不可用/调用失败 → 全部启发式
    for s in sites:
        host = (s.get("host") or "").strip().lower()
        if not host:
            continue
        if host not in out:
            out[host] = (heuristic_score(s), _heuristic_description(s))
        else:
            score, desc = out[host]
            if score is None:
                out[host] = (heuristic_score(s), desc or _heuristic_description(s))
            elif not desc:
                out[host] = (score, _heuristic_description(s))
    return out


# ---------------------------------------------------------------- 录入核心

def record_feedback(sites: list[dict]) -> dict[str, int]:
    """录入一批站点信誉:LLM 打分(失败回退启发式)→ 入库。返回 {host: score}。

    sites: [{host, url, query, page_class, reason, downloaded, agent_ok,
             merge_ok, fake, skipped, outcome, note}]
    按 host 去重,最多 _MAX_BATCH 条;永不抛异常(录入失败不阻塞任务)。
    """
    if not sites:
        return {}
    # 按 host 去重(保留最后一次的信号),cap 数量;受保护站点(教育/AI/邮箱等敏感
    # 账号站)永不录入 —— 不允许被标成噪音/差站(用户本地 cookie 分类产出)
    from .protected import is_protected

    dedup: dict[str, dict] = {}
    for s in sites:
        host = (s.get("host") or "").strip().lower()
        if not host or is_protected(host):
            continue
        dedup[host] = s
    picked = list(dedup.values())[:_MAX_BATCH]
    scored = score_sites_with_llm(picked)
    result: dict[str, int] = {}
    for s in picked:
        host = s.get("host", "").strip().lower()
        score, desc = scored.get(host, (heuristic_score(s), _heuristic_description(s)))
        try:
            e = store.record(
                host=host, url=s.get("url", ""), score=score, description=desc,
                query=s.get("query", ""),
                outcome=s.get("outcome") or _outcome_from_signals(s),
                note=s.get("note", ""),
            )
            if "score" in e:
                result[host] = int(e["score"])
        except Exception:
            continue
    return result


def _outcome_from_signals(s: dict) -> str:
    for k in ("downloaded", "agent_ok", "merge_ok"):
        if s.get(k):
            return k
    if s.get("fake"):
        return "fake"
    if s.get("skipped"):
        return str(s["skipped"])
    if s.get("downloaded") is False:
        return "failed"
    return "visited"


# ---------------------------------------------------------------- 工具(Agent/AI 可调用)

@tool(
    "record_site_feedback",
    "站点信誉录入:把本次任务访问过的网站批量交给 LLM 打分(0-9,综合内容获取容易度/"
    "是否虚假/可访问性/实际落地)并生成短描述,存入信誉库。任务层在每次任务结束后自动调用,"
    "一般无需手动调;手动录入可用于补充外部观察。",
    parameters={
        "type": "object",
        "properties": {
            "sites": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "host": {"type": "string", "description": "站点域名(不含协议)"},
                        "url": {"type": "string", "description": "访问的 URL"},
                        "query": {"type": "string", "description": "触发任务查询"},
                        "page_class": {"type": "string", "description": "页面分级: download_page/aggregator/login_required/blocked/unknown/direct_file"},
                        "reason": {"type": "string", "description": "分级原因"},
                        "downloaded": {"type": "boolean", "description": "本次是否成功下载"},
                        "agent_ok": {"type": "boolean", "description": "Agent 路径是否成功"},
                        "merge_ok": {"type": "boolean", "description": "章节拼接是否成功"},
                        "fake": {"type": "boolean", "description": "是否存在虚假/误导(按钮指向书页/HTML 冒充文件等)"},
                        "skipped": {"type": "string", "description": "跳过原因(paid/slow/blocked 等)"},
                        "note": {"type": "string", "description": "备注"},
                    },
                    "required": ["host"],
                },
                "description": "本次访问过的站点列表(≤15 个,按 host 去重)",
            },
        },
        "required": ["sites"],
    },
    category="execute",
    timeout_ms=120_000,
    concurrency_safe=False,
)
def record_site_feedback(sites: list[dict], ctx=None) -> ToolResult:
    result = record_feedback(sites or [])
    if not result:
        return ToolResult.failure("没有可录入的站点(host 为空)")
    lines = [f"已录入 {len(result)} 个站点:"]
    for host, score in sorted(result.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {host} = {score}/9")
    return ToolResult.success("\n".join(lines), data=result)


@tool(
    "site_lookup",
    "站点信誉查询:按查询词(书名/资源类型/站点名等)在信誉库里做向量相似度检索,"
    "返回 top-k 个站点(附带 0-9 评分、短描述、相似度)。"
    "选站/Agent 导航时优先高分站,避开低分(≤2)或描述含'虚假/HTML 冒充/登录墙'的站。"
    "库为空时返回空列表。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索词,如'小说 txt 下载'/'壁纸 高清'"},
            "top_k": {"type": "integer", "description": "返回条数,默认 5"},
            "min_score": {"type": "integer", "description": "最低评分过滤(0-9),默认 0 不过滤"},
        },
        "required": ["query"],
    },
    category="search",
    timeout_ms=30_000,
)
def site_lookup(query: str, top_k: int = 5, min_score: int = 0, ctx=None) -> ToolResult:
    hits = store.lookup(query, top_k=top_k, min_score=min_score)
    if not hits:
        return ToolResult.success("信誉库暂无匹配站点(库为空或相似度过低)")
    lines = [f"站点信誉 top-{len(hits)} (查询: {query}):"]
    for e in hits:
        score = e.get("score")
        score_txt = f"{score}/9" if score is not None else "未评分"
        lines.append(f"  {e.get('host')} | {score_txt} | 相似度 {e.get('similarity', 0):.3f}"
                     + (f" | {e.get('description')}" if e.get("description") else ""))
    return ToolResult.success("\n".join(lines),
                              data=[{"host": e.get("host"), "score": e.get("score"),
                                     "description": e.get("description"),
                                     "similarity": e.get("similarity", 0)} for e in hits])
