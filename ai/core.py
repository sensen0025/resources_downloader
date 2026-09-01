"""AI 核心 — AgentCore 循环(DSH agent-loop 同构)。

单一循环驱动一切:LLM 决策 → schema 校验 → 执行技能 → 回写 → 状态投影 → 停止判定。
规则管道(search/pages/delivery)全是技能;新场景 = 新技能,不改循环。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Optional

from skills.core import ToolResult, get_registry
from .prompts import build_system_prompt
from .state import TaskState

__all__ = ["AgentCore", "HarnessConfig"]


@dataclass
class HarnessConfig:
    max_steps: int = 20
    stall_limit: int = 3
    max_history: int = 30        # 消息历史上限(截断旧工具结果,保审计)
    temperature: float = 0.2
    max_tokens: int = 2048
    llm: Optional[Any] = None    # 可注入 mock LLM(离线测试)


@dataclass
class AgentResult:
    success: bool
    summary: str
    files: list = field(default_factory=list)
    decisions: list = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict:
        return {"success": self.success, "summary": self.summary,
                "files": self.files, "decisions": self.decisions[-20:],
                "error": self.error}


class AgentCore:
    """AI Harness 核心:请求 → 决策循环 → 技能执行 → 结果。"""

    def __init__(self, config: Optional[HarnessConfig] = None) -> None:
        self.config = config or HarnessConfig()
        self.registry = get_registry()
        if self.config.llm is None:
            try:
                from agent.llm import LLMClient

                self.llm = LLMClient()
            except Exception as e:
                self.llm = None
                self._llm_error = str(e)
            else:
                self._llm_error = ""
        else:
            self.llm = self.config.llm
            self._llm_error = ""
        self.state: TaskState = TaskState()
        self.ctx = SimpleNamespace(state=None, session=None)

    # ------------------------------------------------------------ 入口

    def run(self, request: str) -> AgentResult:
        self.state = TaskState(request=request)
        self.ctx.state = self.state
        if self.llm is None:
            return AgentResult(False, f"LLM 不可用: {self._llm_error}",
                               error="NO_LLM")
        system = build_system_prompt(self.registry.catalog(),
                                     self.config.max_steps, self.config.stall_limit)
        history: list[dict] = [{"role": "system", "content": system},
                               {"role": "user", "content": request}]
        stall: dict[str, int] = {}

        for step in range(1, self.config.max_steps + 1):
            decision = self._decide(history)
            if decision is None:
                history.append({"role": "user", "content": "[系统] 决策解析失败,请重新输出合法 JSON"})
                continue

            if decision.get("done"):
                return self._finish(decision)

            skill = decision.get("skill", "")
            if not self.registry.has(skill):
                history.append({"role": "user", "content":
                                f"[系统] 未知技能 {skill!r},只能使用目录里的技能"})
                continue

            # 卡住检测:同一技能+同一参数 N 次
            key = f"{skill}:{json.dumps(decision.get('args', {}), sort_keys=True)[:80]}"
            stall[key] = stall.get(key, 0) + 1
            if stall[key] >= self.config.stall_limit:
                self.state.errors.append(f"卡住: {key} 重复 {self.config.stall_limit} 次")
                return AgentResult(False, f"卡住: 重复调用 {skill}", error="STALLED",
                                   decisions=self.state.decisions,
                                   files=list(self.state.files))

            result = self.registry.invoke(skill, decision.get("args") or {}, ctx=self.ctx)
            self.state.record(decision, result)
            # 对话式回写(非原生 tool-calling,避免 API 要求 tool_call_id)
            history.append({"role": "assistant", "content": json.dumps(decision, ensure_ascii=False)})
            history.append({"role": "user", "content": f"[工具 {skill} 返回] {result}"})
            history = self._trim(history)

            if result.ok:
                # 文件落地且意图满足 → 视为成功路径(最终 done 由模型确认)
                new_files = getattr(result, "data", None)
                if isinstance(new_files, dict) and new_files.get("path"):
                    p = new_files["path"]
                    if p not in self.state.files:
                        self.state.files.append(p)

        self.state.errors.append(f"达到最大步数 {self.config.max_steps}")
        return AgentResult(False, "达到最大步数未完成",
                           error="BUDGET", decisions=self.state.decisions,
                           files=list(self.state.files))

    # ------------------------------------------------------------ 内部

    def _decide(self, history: list[dict]) -> Optional[dict]:
        try:
            text = self.llm.chat(history, temperature=self.config.temperature,
                                 max_tokens=self.config.max_tokens)
        except Exception as e:
            return None
        try:
            # 复用 agent.agent 的容错解析(截断补全/尾文本/围栏/单引号),
            # 避免同一份坏输出在 AI 主路径上也白白重试
            from agent.agent import _parse_decision

            return _parse_decision(text)
        except Exception:
            return None

    def _trim(self, history: list[dict]) -> list[dict]:
        if len(history) <= self.config.max_history:
            return history
        # 保留 system + 最近 N 条;中间旧的 tool 结果压缩成一行审计摘要
        head = history[:1]
        tail = history[-(self.config.max_history - 2):]
        n_dropped = len(history) - 1 - len(tail)
        summary = {"role": "user", "content":
                   f"[系统] 已省略 {n_dropped} 条早期步骤(见最终审计 decisions)。当前任务状态: "
                   f"文件={self.state.files}, 错误={self.state.errors[-3:]}"}
        return head + [summary] + tail

    def _finish(self, decision: dict) -> AgentResult:
        success = bool(decision.get("success", False))
        summary = str(decision.get("summary", ""))
        # 诚实校验:模型说成功但没文件 → 拒绝(下载探针兜底)
        if success and not self.state.files:
            self.state.errors.append("模型声明成功但无文件落地")
            success = False
            summary = (summary + " | 但无文件落地,判定失败").strip()
        return AgentResult(success, summary, files=list(self.state.files),
                           decisions=self.state.decisions)


def _parse_decision(text: str) -> dict:
    """容错解析决策 JSON(复用 agent 的解析思路)。"""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    cleaned = re.sub(r"```(?:json)?", "", text).strip("` \n")
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"无法解析决策 JSON") from exc
