"""技能层 — DSH 式工具内核 + 技能包(mail / captcha / security)。

`skills/core.py` 是对齐 DeepSeek Harness defineTool 的 Python 工具 DSL:
单点定义(name/description/参数 schema/category/超时/并发安全)、
执行前强校验、注册表即唯一真相源。Agent 通过注册表调用全部技能。
"""

from .core import (
    TOOL_KINDS,
    ToolArgsError,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    get_registry,
    tool,
    validate_args,
)

__all__ = [
    "tool",
    "ToolSpec",
    "ToolResult",
    "ToolArgsError",
    "ToolRegistry",
    "get_registry",
    "validate_args",
    "TOOL_KINDS",
]

# 恢复上次的技能禁用状态(mod 式卸载在重启后依然生效)
try:
    from .manager import apply_state

    apply_state()
except Exception:  # 状态文件损坏/环境不完整时不阻断
    pass
