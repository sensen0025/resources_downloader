"""技能内核 — 「AI 核心给 tools」的 Python 实现。

对齐 DeepSeek Harness `@deepseek-ai/dsh-tools`(packages/core/tools)的架构:
- **单点定义**:一个工具的全部契约(name/description/parameters 校验/output/render/超时/并发安全)
  在 `@tool` 装饰器一处声明,像 DSH 的 `defineTool`;
- **执行前强校验**:LLM 生成的参数先过 schema(违规即拒绝,返回路径化 violations,
  像 DSH 的 `ToolArgsError`),绝不把脏参数送进 handler;
- **展示与执行分离**:`category`(ToolCallKind 同款词汇)与可选的 present_* 纯函数,
  只负责"怎么显示",与执行逻辑解耦(重放安全);
- **注册即目录**:`ToolRegistry.catalog()` 产出 OpenAI 格式工具列表直接喂 LLM,
  注册表是唯一真相源 —— 模型看到的、执行的、展示的来自同一份契约。
"""

from __future__ import annotations

import inspect
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

__all__ = [
    "ToolSpec", "ToolResult", "ToolArgsError", "validate_args",
    "ToolRegistry", "tool", "get_registry",
]

# 工具调用分类(对齐 DSH ToolCallKind,UI/日志据此选图标与呈现)
TOOL_KINDS = ("read", "edit", "delete", "move", "search", "execute", "fetch", "other")


# ---------------------------------------------------------------- 结果协议

@dataclass
class ToolResult:
    """统一工具结果:ok + data(结构化)+ error + message(模型可见渲染)。

    对齐 DSH 的 canonical value / render 分离:data 是规范值(给代码消费),
    message 是模型可见的最终呈现(给 LLM 反馈)。
    """

    ok: bool
    data: Any = None
    error: str = ""
    message: str = ""

    @classmethod
    def success(cls, message: str, data: Any = None) -> "ToolResult":
        return cls(ok=True, data=data, message=message)

    @classmethod
    def failure(cls, message: str, error: str = "", data: Any = None) -> "ToolResult":
        return cls(ok=False, error=error or message, message=message, data=data)

    def __str__(self) -> str:
        return self.message


# ---------------------------------------------------------------- 参数校验

def _type_matches(node: dict, value: Any) -> bool:
    t = node.get("type")
    if t == "string":
        return isinstance(value, str)
    if t in ("number", "integer"):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "null":
        return value is None
    if t == "array":
        return isinstance(value, list)
    if t == "object":
        return isinstance(value, dict)
    return True


def _validate_value(node: dict, value: Any, path: str, violations: list[str]) -> None:
    if not _type_matches(node, value):
        violations.append(f"{path}: 期望 {node.get('type')},实际 {type(value).__name__}")
        return
    if "enum" in node and value not in node["enum"]:
        violations.append(f"{path}: 不在允许值 {node['enum']} 内")
    if node.get("type") == "array" and "items" in node and isinstance(value, list):
        for i, item in enumerate(value):
            _validate_value(node["items"], item, f"{path}[{i}]", violations)
    if node.get("type") == "object" and isinstance(value, dict):
        props = node.get("properties", {})
        for key, sub in props.items():
            if key in value:
                _validate_value(sub, value[key], f"{path}.{key}", violations)
        for req in node.get("required", []):
            if req not in value:
                violations.append(f"{path}: 缺少必填字段 '{req}'")


def validate_args(parameters: dict, args: Any) -> list[str]:
    """校验模型生成的参数,返回路径化违规列表(空 = 合法)。"""
    if not isinstance(args, dict):
        return ["参数必须是 JSON 对象"]
    violations: list[str] = []
    root = {"type": "object", "properties": parameters.get("properties", {}),
            "required": parameters.get("required", [])}
    _validate_value(root, args, "args", violations)
    return violations


class ToolArgsError(ValueError):
    """非法参数异常(对齐 DSH ToolArgsError)。"""

    def __init__(self, violations: list[str]) -> None:
        super().__init__("; ".join(violations))
        self.violations = violations


# ---------------------------------------------------------------- 工具定义

@dataclass
class ToolSpec:
    """一个工具的完整契约(对齐 DSH ToolDefinition)。"""

    name: str
    description: str
    parameters: dict                        # JSON Schema 子集: {properties, required}
    output_schema: Optional[dict] = None    # 规范输出 schema(可选)
    category: str = "other"                 # read/search/fetch/execute/...
    timeout_ms: Optional[int] = None        # 合作式超时预算
    is_concurrency_safe: bool = True        # 是否可与同批动作并行
    handler: Optional[Callable] = None      # 执行函数: handler(args, ctx=None)
    present_call: Optional[Callable] = None    # 纯函数: 调用展示 -> dict|None
    present_result: Optional[Callable] = None  # 纯函数: 结果展示 -> str|None

    def catalog_entry(self) -> dict:
        """OpenAI 格式工具定义(直接进 LLM 的 tools 列表)。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", **self.parameters},
            },
        }


# ---------------------------------------------------------------- 注册表

class ToolRegistry:
    """工具注册表:唯一真相源 —— catalog(给模型)/ invoke(执行)/ 展示(给 UI)。

    enable/disable 支持「mod 式卸载」:停用的工具不出现在 catalog,
    invoke 直接拒绝 —— 由 skills/manager.py 驱动。
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._disabled: set[str] = set()
        self._lock = threading.Lock()

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"工具 {spec.name!r} 重复注册")
        if spec.category not in TOOL_KINDS:
            raise ValueError(f"工具 {spec.name!r} 的 category {spec.category!r} 不在 {TOOL_KINDS}")
        with self._lock:
            self._tools[spec.name] = spec

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def all(self) -> list[ToolSpec]:
        return [s for n, s in self._tools.items() if n not in self._disabled]

    def disable(self, name: str) -> None:
        """停用工具(mod 卸载):catalog 消失,invoke 拒绝。"""
        with self._lock:
            self._disabled.add(name)

    def enable(self, name: str) -> None:
        with self._lock:
            self._disabled.discard(name)

    def disabled_names(self) -> list[str]:
        return sorted(self._disabled)

    def catalog(self, names: Optional[list[str]] = None) -> list[dict]:
        """给 LLM 的工具目录(OpenAI 格式);names=None 时全部(不含停用)。"""
        specs = self._tools.values() if names is None else [self._tools[n] for n in names if n in self._tools]
        return [s.catalog_entry() for s in specs if s.name not in self._disabled]

    def invoke(self, name: str, args: dict, ctx: Any = None) -> ToolResult:
        """校验 → 执行 → 规范化结果(对齐 DSH: execute 前必须 validate)。"""
        spec = self._tools.get(name)
        if spec is None or spec.handler is None:
            return ToolResult.failure(f"未知工具 {name!r}")
        if name in self._disabled:
            return ToolResult.failure(f"工具 {name!r} 已停用(该技能被卸载/禁用)")
        violations = validate_args(spec.parameters, args or {})
        if violations:
            return ToolResult.failure(
                f"工具 {name} 参数不合法: {'; '.join(violations)}",
                error="INVALID_ARGS",
            )
        try:
            # 只注入 handler 签名内的参数:LLM 偶尔带 schema 外的多余字段
            # (如 human 带 success),硬塞会导致 TypeError,这里静默忽略
            sig = inspect.signature(spec.handler)
            params = set(sig.parameters)
            kwargs = {k: v for k, v in (args or {}).items() if k in params}
            # 按名注入 ctx(browser-use 的"特殊参数按名注入"模式)
            if ctx is not None and "ctx" in params:
                kwargs["ctx"] = ctx
            result = spec.handler(**kwargs)
            if not isinstance(result, ToolResult):
                result = ToolResult.success(str(result), data=result)
            if spec.present_result:
                rendered = spec.present_result(args or {}, result)
                if rendered:
                    result.message = rendered
            return result
        except ToolArgsError as e:
            return ToolResult.failure(f"工具 {name} 参数不合法: {'; '.join(e.violations)}",
                                      error="INVALID_ARGS")
        except Exception as e:  # handler 内部错误统一规范化
            return ToolResult.failure(f"工具 {name} 执行失败: {type(e).__name__}: {str(e)[:200]}",
                                      error=type(e).__name__)


# 默认全局注册表(单例)
_DEFAULT: Optional[ToolRegistry] = None
_DEFAULT_LOCK = threading.Lock()


def get_registry() -> ToolRegistry:
    global _DEFAULT
    if _DEFAULT is None:
        with _DEFAULT_LOCK:
            if _DEFAULT is None:
                _DEFAULT = ToolRegistry()
    return _DEFAULT


def tool(name: str, description: str, parameters: Optional[dict] = None, *,
         output_schema: Optional[dict] = None, category: str = "other",
         timeout_ms: Optional[int] = None, concurrency_safe: bool = True,
         present_call: Optional[Callable] = None, present_result: Optional[Callable] = None,
         registry: Optional[ToolRegistry] = None) -> Callable:
    """@tool 装饰器 —— 单点定义一个工具的完整契约(对齐 DSH defineTool)。"""

    def deco(fn: Callable) -> Callable:
        spec = ToolSpec(
            name=name,
            description=description,
            parameters=parameters or {"type": "object", "properties": {}, "required": []},
            output_schema=output_schema,
            category=category,
            timeout_ms=timeout_ms,
            is_concurrency_safe=concurrency_safe,
            handler=fn,
            present_call=present_call,
            present_result=present_result,
        )
        (registry or get_registry()).register(spec)
        return fn

    return deco
