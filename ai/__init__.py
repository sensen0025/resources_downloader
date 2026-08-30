"""AI 核心层 — AgentCore 循环 + AI 决策型技能(v6)。

AI 是唯一大脑,搜索/分析/下载/验证全是技能;意图绑定是状态不是参数。
"""

from .core import AgentCore, AgentResult, HarnessConfig
from .state import ResourceIntent, TaskState

# 注册 AI 决策型技能(intent_parse/search/verify_file/inspect_archive/batch_research)
import ai.skills as _skills  # noqa: F401  (side-effect 注册)

__all__ = ["AgentCore", "AgentResult", "HarnessConfig", "ResourceIntent", "TaskState"]
