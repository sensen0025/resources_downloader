"""下载安全查毒技能 — 配置加载(环境变量 + 项目根 .env,复用 mail 的极简解析)。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from skills.mail.config import load_dotenv

__all__ = ["SecurityConfig"]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]  # resource-hub/
_DEFAULT_RULES = Path(__file__).resolve().parent / "rules"


@dataclass
class SecurityConfig:
    """查毒引擎配置。

    - clamd:ClamAV 守护进程(推荐,病毒库常驻内存,按 INSTREAM 协议流式扫描);
    - clamscan:ClamAV 命令行(clamd 不可用时兜底,逐文件启动);
    - yara:yara-python(可选) + 内置规则目录 + 用户规则目录;
    - heuristic:内置启发式(纯标准库,永远可用)。
    """

    # clamd
    clamd_host: str = "127.0.0.1"
    clamd_port: int = 3310
    clamd_socket: str = ""          # unix socket(Linux/macOS),非空则优先于 TCP
    clamd_enabled: bool = True
    # clamscan
    clamscan_path: str = ""         # 空 = 自动在 PATH 找 clamscan
    # yara
    yara_enabled: bool = True
    yara_rules_dir: str = ""        # 空 = 使用内置 rules/;可追加用户规则目录(路径列表用 ; 分隔)
    # heuristic
    heuristic_enabled: bool = True
    # 通用
    timeout: int = 60               # 单引擎超时(秒)
    strict: bool = True             # suspicious 是否判失败(scan_file 默认)

    @classmethod
    def from_env(cls, overrides: Optional[dict[str, object]] = None) -> "SecurityConfig":
        load_dotenv()
        o = overrides or {}

        def get(key: str, default: str = "") -> str:
            v = o.get(key)
            if v is not None:
                return str(v)
            return os.environ.get(key, default)

        def get_int(key: str, default: int) -> int:
            try:
                return int(get(key, str(default)))
            except ValueError:
                return default

        def get_bool(key: str, default: bool) -> bool:
            v = get(key, "1" if default else "0").strip().lower()
            return v not in ("0", "false", "no", "off")

        return cls(
            clamd_host=get("SECURITY_CLAMD_HOST", "127.0.0.1") or "127.0.0.1",
            clamd_port=get_int("SECURITY_CLAMD_PORT", 3310),
            clamd_socket=get("SECURITY_CLAMD_SOCKET"),
            clamd_enabled=get_bool("SECURITY_CLAMD_ENABLED", True),
            clamscan_path=get("SECURITY_CLAMSCAN_PATH"),
            yara_enabled=get_bool("SECURITY_YARA_ENABLED", True),
            yara_rules_dir=get("SECURITY_YARA_RULES_DIR"),
            heuristic_enabled=get_bool("SECURITY_HEURISTIC_ENABLED", True),
            timeout=get_int("SECURITY_TIMEOUT", 60),
            strict=get_bool("SECURITY_STRICT", True),
        )

    def effective_clamscan(self) -> Optional[str]:
        """返回可用的 clamscan 可执行文件路径(显式配置 > PATH 查找)。"""
        if self.clamscan_path:
            p = Path(self.clamscan_path)
            if p.exists():
                return str(p)
        import shutil

        found = shutil.which("clamscan")
        if found:
            return found
        # Windows 常见安装位置兜底
        for cand in (
            r"C:\Program Files\ClamAV\clamscan.exe",
            r"C:\Program Files (x86)\ClamAV\clamscan.exe",
            r"C:\tools\clamav\clamscan.exe",
            "/usr/bin/clamscan",
            "/usr/local/bin/clamscan",
            "/opt/homebrew/bin/clamscan",
        ):
            if Path(cand).exists():
                return cand
        return None

    def yara_rule_dirs(self) -> list[Path]:
        """内置规则目录 + 用户配置目录(存在才返回)。"""
        dirs = [_DEFAULT_RULES]
        for raw in self.yara_rules_dir.split(";"):
            raw = raw.strip()
            if not raw:
                continue
            p = Path(raw)
            if not p.is_absolute():
                p = _PROJECT_ROOT / p
            if p.is_dir():
                dirs.append(p)
        return dirs
