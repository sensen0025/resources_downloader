"""视觉内容查询 — 验证下载图片"内容对不对"(营业执照 vs 皮肤)。

OpenAI 兼容 image_url(与 tools_vision 同款模式),用于 verify_file 的
内容级验证:格式验证通过后,视觉模型判断图片内容是否与意图匹配。
"""

from __future__ import annotations

import base64
import json
import os
import re
from typing import Optional

import requests

from agent.llm import _config
from skills.mail.config import load_dotenv

__all__ = ["image_query", "verify_image_content"]

_VISION_PROMPT_TPL = (
    "这是一张图片。请判断它是否与目标主题相关。\n"
    "目标主题: {subject}\n"
    "判断标准: 内容是否为{subject}(或直接相关物,如游戏皮肤、角色立绘、截图等)。\n"
    "只输出一个 JSON: {{\"is_match\": true/false, \"content\": \"一句话描述图片实际内容\", "
    "\"reason\": \"判定理由\"}}\n"
    "注意: 无关图片(证件照、营业执照、广告、风景、随机照片等)必须 is_match=false。"
)


def image_query(img_bytes: bytes, prompt: str, timeout: float = 60.0) -> Optional[str]:
    """发送图片+提示词到视觉模型,返回模型文本;失败返回 None。"""
    key, base, _ = _config()
    if not key:
        return None
    load_dotenv()
    vision_model = os.environ.get("LLM_VISION_MODEL", "deepseek-v4-flash-vision-exp")
    b64 = base64.b64encode(img_bytes).decode()
    payload = {
        "model": vision_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }
        ],
        "max_tokens": 512,
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
        return resp.json()["choices"][0]["message"]["content"]
    except Exception:
        return None


def verify_image_content(img_bytes: bytes, subject: str) -> dict:
    """视觉验证图片内容与主题是否匹配。

    返回 {"ok": bool, "content": 描述, "reason": 理由}。
    视觉模型不可用/失败时返回 {"ok": None}(调用方按"不阻塞"降级)。
    """
    text = image_query(img_bytes, _VISION_PROMPT_TPL.format(subject=subject))
    if not text:
        return {"ok": None, "content": "", "reason": "视觉模型不可用"}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {"ok": None, "content": text[:100], "reason": "视觉输出非 JSON,降级"}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"ok": None, "content": text[:100], "reason": "视觉输出解析失败,降级"}
    return {
        "ok": bool(data.get("is_match")),
        "content": str(data.get("content", ""))[:200],
        "reason": str(data.get("reason", ""))[:200],
    }
