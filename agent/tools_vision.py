"""视觉模型验证码识别(OpenAI 兼容 image_url)—— 本地 OCR 的降级兜底。"""

from __future__ import annotations

import base64
import os
import re
from typing import Optional

import requests

from .llm import _config
from skills.mail.config import load_dotenv


def solve_captcha_vlm(img_bytes: bytes, charset: Optional[str] = None,
                      timeout: float = 60) -> Optional[str]:
    """用视觉模型识别验证码。失败/超时返回 None。"""
    key, base, _ = _config()
    if not key:
        return None
    load_dotenv()
    vision_model = os.environ.get("LLM_VISION_MODEL", "deepseek-v4-flash-vision-exp")
    b64 = base64.b64encode(img_bytes).decode()
    prompt = "这张图片是一个图形验证码。请只输出验证码里的字符,不要任何解释、引号或标点。"
    if charset:
        prompt += f"只允许出现这些字符:{charset}。"
    payload = {
        "model": vision_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }
        ],
        "max_tokens": 256,  # 推理模型会先输出 reasoning_content,预算要给足
        "temperature": 0.0,
    }
    try:
        resp = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
        if resp.status_code != 200:
            return None
        content = resp.json()["choices"][0]["message"]["content"]
    except Exception:
        return None
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff+\-]", "", content)
    return text or None
