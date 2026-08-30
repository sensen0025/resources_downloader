"""Webhook 回调 — 任务完成/失败时主动推送给客户。

- 签名:`X-RH-Signature: sha256=<hex>`,HMAC-SHA256(secret, body);
- 重试:指数退避 3 次(1s/3s/7s),后台线程发送不阻塞任务;
- SSRF 防护:仅允许 http/https;默认放行内网(个人联调方便),可用
  RH_WEBHOOK_ALLOW_PRIVATE=0 收紧。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Optional

__all__ = ["get_webhook_secret", "sign", "deliver_webhook", "is_safe_callback_url"]

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def get_webhook_secret() -> str:
    """签名密钥:RH_WEBHOOK_SECRET > RH_API_SECRET > data/.webhook_secret(自动生成)。"""
    env = os.environ.get("RH_WEBHOOK_SECRET") or os.environ.get("RH_API_SECRET")
    if env:
        return env
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    secret_file = _DATA_DIR / ".webhook_secret"
    if secret_file.exists():
        return secret_file.read_text(encoding="utf-8").strip()
    secret = secrets.token_hex(32)
    secret_file.write_text(secret, encoding="utf-8")
    return secret


def sign(payload: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def is_safe_callback_url(url: str) -> bool:
    try:
        p = urllib.parse.urlsplit(url)
    except Exception:
        return False
    if p.scheme not in ("http", "https") or not p.netloc:
        return False
    if os.environ.get("RH_WEBHOOK_ALLOW_PRIVATE", "1") == "1":
        return True
    # 收紧模式:禁止内网/回环(SSRF)
    host = (p.hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local"):
        return False
    if host.startswith("10.") or host.startswith("192.168.") or host.startswith("172."):
        return False
    return True


def deliver_webhook(callback_url: str, payload: dict, secret: Optional[str] = None,
                    retries: int = 3) -> None:
    """后台发送回调(daemon 线程),指数退避重试。失败仅打印,不阻塞任务。"""
    if not is_safe_callback_url(callback_url):
        print(f"[webhook] 拒绝回调地址(非 http/https): {callback_url[:80]}", flush=True)
        return

    def _send() -> None:
        import requests

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        secret = secret or get_webhook_secret()
        headers = {
            "Content-Type": "application/json",
            "X-RH-Signature": f"sha256={sign(body, secret)}",
            "X-RH-Event": payload.get("event", "task.finished"),
        }
        for attempt in range(retries):
            try:
                r = requests.post(callback_url, data=body, headers=headers, timeout=15)
                if r.status_code < 300:
                    return
                print(f"[webhook] HTTP {r.status_code}(第 {attempt + 1} 次)", flush=True)
            except Exception as e:
                print(f"[webhook] 发送失败({attempt + 1}/{retries}): {type(e).__name__}: {str(e)[:100]}", flush=True)
            if attempt < retries - 1:
                time.sleep(1 * (3 ** attempt))  # 1s / 3s / 7s

    threading.Thread(target=_send, daemon=True).start()
