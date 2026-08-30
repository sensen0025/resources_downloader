"""AI 核心 — 任务状态与资源意图(智能的载体)。

意图绑定是状态不是一次性参数:AgentCore 首回合解析意图写入 state,
之后每一次下载决策与完成验证都对照 state.intent 裁决。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = ["ResourceIntent", "TaskState"]


@dataclass
class ResourceIntent:
    """AI 意图解析结果(强绑定格式 + 未知格式兜底)。"""

    query: str = ""
    kind: str = "generic"                    # schematic / image / document / media / generic
    preferred_exts: tuple[str, ...] = ()     # 强绑定: 投影 → (.litematic,)
    accept_exts: tuple[str, ...] = ()        # 兜底:   投影 → (.schematic,.schem,.zip)
    binding: str = "lenient"                 # strict=只要 preferred / lenient=可兜底
    format_unknown: bool = False             # 无法确定格式 → 下载后裁决
    sources_hint: tuple[str, ...] = ()       # 建议来源站(领域知识: 皮肤→皮肤站, 投影→蓝图站)
    constraints: dict = field(default_factory=dict)  # free_only / no_paywall / min_size / count

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "kind": self.kind,
            "preferred_exts": list(self.preferred_exts),
            "accept_exts": list(self.accept_exts),
            "binding": self.binding,
            "format_unknown": self.format_unknown,
            "sources_hint": list(self.sources_hint),
            "constraints": self.constraints,
        }

    def ext_matches(self, url_or_path: str, strict_only: bool = False) -> str:
        """判断 URL/路径扩展名:命中 preferred 返回 'preferred',命中 accept 返回 'accept'。"""
        low = url_or_path.lower()
        for e in self.preferred_exts:
            if low.endswith(e):
                return "preferred"
        if not strict_only:
            for e in self.accept_exts:
                if low.endswith(e):
                    return "accept"
        return ""


@dataclass
class TaskState:
    """一次 AgentCore 任务的完整状态(历史决策、候选、文件全可审计)。"""

    request: str = ""
    intent: Optional[ResourceIntent] = None
    candidates: list = field(default_factory=list)      # list[SearchResult]
    probed: dict = field(default_factory=dict)          # url -> ProbeInfo
    files: list[str] = field(default_factory=list)      # 已下载文件路径
    errors: list[str] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)  # 全链路审计
    summary: str = ""
    done: bool = False
    success: bool = False

    def record(self, decision: dict, result: Any) -> None:
        self.decisions.append({
            "skill": decision.get("skill"),
            "args": decision.get("args", {}),
            "result_ok": bool(getattr(result, "ok", False)),
            "result": str(getattr(result, "message", result))[:200],
        })
