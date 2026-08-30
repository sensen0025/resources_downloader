"""可选 VLM 兜底:本地 Ollama(Qwen2.5-VL 等)或任意 OpenAI 兼容视觉端点。

纯 urllib 实现,零第三方依赖。未配置时 solver 自动跳过 VLM 路径。

配置(环境变量):
  CAPTCHA_VLM_URL    e.g. http://localhost:11434/v1/chat/completions
  CAPTCHA_VLM_MODEL  e.g. qwen2.5-vl:7b
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.request
from typing import Optional


def vlm_available() -> bool:
    return bool(os.environ.get("CAPTCHA_VLM_URL") and os.environ.get("CAPTCHA_VLM_MODEL"))


def solve_with_vlm(img_bytes: bytes, charset: Optional[str] = None, timeout: float = 30) -> Optional[str]:
    """调用视觉大模型识别验证码,失败/超时返回 None。"""
    url = os.environ.get("CAPTCHA_VLM_URL", "")
    model = os.environ.get("CAPTCHA_VLM_MODEL", "")
    if not url or not model:
        return None

    b64 = base64.b64encode(img_bytes).decode()
    prompt = "这张图片是一个图形验证码。请只输出验证码里的字符,不要任何解释、引号或标点。"
    if charset:
        prompt += f"只允许出现这些字符:{charset}。"

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }
        ],
        "max_tokens": 16,
        "temperature": 0.0,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        content = data["choices"][0]["message"]["content"]
    except Exception:
        return None

    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff+\-]", "", content)
    return text or None
