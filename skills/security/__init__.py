"""下载安全查毒技能 — 对外 API。

    from skills.security import scan_file, detect_engines, ScanResult

底层引擎(纯规则能力)在这里;AI 决策型工具 `scan_file` 在 `ai/skills.py` 注册
(import ai 时自动进 AgentCore 目录),下载链路闸门在 `agent/tasks/fetch_resource.py`。
"""

from .config import SecurityConfig
from .scanner import (
    ENGINE_NAMES,
    VERDICTS,
    EngineFinding,
    ScanResult,
    detect_engines,
    install_hint,
    scan_file,
)

__all__ = [
    "SecurityConfig",
    "EngineFinding",
    "ScanResult",
    "scan_file",
    "detect_engines",
    "install_hint",
    "VERDICTS",
    "ENGINE_NAMES",
]
__version__ = "0.1.0"
