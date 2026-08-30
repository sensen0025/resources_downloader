"""配置加载:环境变量 + 项目根 .env(极简 .env 解析,无需第三方依赖)。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

__all__ = ["MailConfig", "load_dotenv"]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]  # resource-hub/


def load_dotenv(path: Optional[Path | str] = None) -> None:
    """按优先级加载 .env:显式 path > 项目根 > 当前目录。已存在的环境变量不覆盖。"""
    candidates: list[Path] = []
    if path:
        candidates.append(Path(path))
    candidates.append(_PROJECT_ROOT / ".env")
    candidates.append(Path.cwd() / ".env")

    for p in candidates:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
        return  # 只加载找到的第一个 .env


@dataclass
class MailConfig:
    host: str
    email: str
    password: str
    port: int = 993
    folder: str = "INBOX"
    use_ssl: bool = True
    starttls: bool = False
    timeout: int = 30

    @classmethod
    def from_env(cls, overrides: Optional[dict[str, object]] = None) -> "MailConfig":
        """从环境变量构建配置;overrides 的 key 为环境变量名,可被 CLI 参数覆盖。"""
        load_dotenv()
        o = overrides or {}

        def get(key: str, default: Optional[str] = None) -> Optional[str]:
            v = o.get(key)
            if v is not None:
                return str(v)
            return os.environ.get(key, default)

        port = int(get("MAIL_IMAP_PORT", "993") or 993)
        timeout = int(get("MAIL_TIMEOUT", "30") or 30)
        use_ssl = get("MAIL_USE_SSL", "1") not in ("0", "false", "False")
        starttls = get("MAIL_STARTTLS", "0") in ("1", "true", "True")
        return cls(
            host=get("MAIL_IMAP_HOST", "") or "",
            email=get("MAIL_EMAIL", "") or "",
            password=get("MAIL_PASSWORD", "") or "",
            port=port,
            folder=get("MAIL_FOLDER", "INBOX") or "INBOX",
            use_ssl=use_ssl,
            starttls=starttls,
            timeout=timeout,
        )

    def require_credentials(self) -> None:
        missing = [name for name, val in (("host", self.host), ("email", self.email), ("password", self.password)) if not val]
        if missing:
            raise SystemExit(
                f"缺少 IMAP 配置: {', '.join(missing)}。请设置环境变量或在项目根放 .env(参考 .env.example)。"
            )
