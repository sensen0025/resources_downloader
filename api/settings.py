"""网页控制台配置层 — .env 读写(保留注释/顺序)+ 脱敏 + 立即生效。

- 读取:解析项目根 .env,保留注释与空行(写回不丢);
- 应用:PUT 时同步更新 os.environ(同进程立即生效,不用重启 uvicorn);
- 脱敏:GET 只回 key 是否存在 + 掩码(如 sk-****abcd),不回明文;
- 约定:提交值为 "" → 清除该配置;值为 None/缺失 → 不修改。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from skills.mail.config import load_dotenv

__all__ = [
    "ENV_PATH",
    "read_env",
    "get_masked",
    "update_env",
    "env_status",
]

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"

# 网页可配置项:字段名 → (env key, 是否敏感)
SETTINGS_SCHEMA: dict[str, tuple[str, bool]] = {
    # LLM
    "llm_api_key": ("LLM_API_KEY", True),
    "llm_base_url": ("LLM_BASE_URL", False),
    "llm_model": ("LLM_MODEL", False),
    # 邮箱(IMAP 收验证码)
    "mail_imap_host": ("MAIL_IMAP_HOST", False),
    "mail_imap_port": ("MAIL_IMAP_PORT", False),
    "mail_email": ("MAIL_EMAIL", False),
    "mail_password": ("MAIL_PASSWORD", True),
    # 代理(VPN)
    "proxy_url": ("RH_PROXY_URL", False),
    # 令牌认证开关
    "api_secret": ("RH_API_SECRET", True),
}


def read_env(path: Optional[str | Path] = None) -> list[tuple[str, str]]:
    """解析 .env,返回 [(key, value)] 保持文件顺序(跳过注释/空行)。"""
    p = Path(path) if path else ENV_PATH
    out: list[tuple[str, str]] = []
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out.append((k.strip(), v.strip().strip('"').strip("'")))
    return out


def _current(key: str) -> str:
    load_dotenv()
    return os.environ.get(key, "")


def _mask(value: str) -> str:
    """敏感值掩码:sk-abc... → sk-****cdef。"""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * 6
    return value[:3] + "*" * 4 + value[-4:]


def get_masked(path: Optional[str | Path] = None) -> dict:
    """网页配置页视图:每个字段 {set: bool, value: 掩码或明文(非敏感), env: key}。"""
    p = Path(path) if path else ENV_PATH
    env = dict(read_env(p))
    # 优先级:环境变量 > .env 文件(运行中的进程可能改了 env 未落盘)
    out: dict[str, dict] = {}
    for field, (key, sensitive) in SETTINGS_SCHEMA.items():
        val = os.environ.get(key, env.get(key, ""))
        out[field] = {
            "set": bool(val),
            "value": _mask(val) if sensitive and val else val,
            "sensitive": sensitive,
            "env": key,
        }
    return out


def update_env(updates: dict, path: Optional[str | Path] = None) -> dict:
    """应用配置:updates 的 value 为 None=不改,""=清除,其他=写入。

    同时更新 os.environ(立即生效)与 .env 文件(持久化)。
    返回实际改动列表 [{field, env, action}]。
    """
    p = Path(path) if path else ENV_PATH
    changes: list[dict] = []
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []

    def set_in_file(key: str, value: str) -> None:
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
        new_line = f"{key}={value}"
        for i, ln in enumerate(lines):
            if pattern.match(ln):
                lines[i] = new_line
                return
        lines.append(new_line)  # 不存在 → 追加

    def del_in_file(key: str) -> None:
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
        for i in range(len(lines) - 1, -1, -1):
            if pattern.match(lines[i]):
                lines.pop(i)

    for field, value in (updates or {}).items():
        if field not in SETTINGS_SCHEMA:
            continue
        key, _ = SETTINGS_SCHEMA[field]
        if value is None:
            continue  # 未提交 → 不修改
        value = str(value).strip()
        if value == "":
            os.environ.pop(key, None)
            del_in_file(key)
            changes.append({"field": field, "env": key, "action": "cleared"})
        else:
            os.environ[key] = value
            set_in_file(key, value)
            changes.append({"field": field, "env": key, "action": "set"})

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return changes


def env_status() -> dict:
    """状态栏摘要(不泄露明文)。"""
    masked = get_masked()
    return {
        "llm_key_set": bool(masked["llm_api_key"]["set"]),
        "mail_set": bool(masked["mail_email"]["set"] and masked["mail_password"]["set"]),
        "proxy_url": masked["proxy_url"]["value"] if masked["proxy_url"]["set"] else "",
        "token_mode": bool(masked["api_secret"]["set"]),
    }
