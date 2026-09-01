"""重要站点保护 — 排除列表(对用户敏感/有价值的站点,不允许被当作噪音/差站录入)。

来源:用户本地 cookie 扫描分类(教育/CANVAS、AI 平台、邮箱、社交、支付等)。
文件:data/protected_sites.json {patterns: [regex...]} —— 命中即受保护:
- record_feedback 跳过受保护站点(永不给他们打低分/标噪音);
- 供后续 Agent 登录态保护使用(不在本模块职责内,但列表共用)。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

__all__ = ["protected_patterns", "is_protected"]

_DATA = Path(__file__).resolve().parents[2] / "data" / "protected_sites.json"

_cache: dict = {"mtime": 0.0, "patterns": []}


def protected_patterns() -> list[str]:
    try:
        mtime = _DATA.stat().st_mtime if _DATA.exists() else 0.0
    except OSError:
        mtime = 0.0
    if _cache["mtime"] != mtime:
        pats: list[str] = []
        if _DATA.exists():
            try:
                import json

                pats = list(json.loads(_DATA.read_text(encoding="utf-8")).get("patterns") or [])
            except Exception:
                pats = []
        _cache["mtime"] = mtime
        _cache["patterns"] = pats
    return _cache["patterns"]


def is_protected(host: str) -> bool:
    """host(注册域或完整主机名)是否受保护(教育/AI/邮箱/社交/支付等敏感站点)。

    子域处理:www.google.com / chat.deepseek.com / canvas.instructure.com
    逐级缩短后都要能命中保护规则(如 ^google / deepseek / instructure)。
    """
    host = (host or "").strip().lower().lstrip(".")
    if not host:
        return False
    parts = host.split(".")
    candidates = [".".join(parts[i:]) for i in range(len(parts))]
    for pat in protected_patterns():
        try:
            rx = re.compile(pat)
            if any(rx.search(c) for c in candidates):
                return True
        except re.error:
            continue
    return False
