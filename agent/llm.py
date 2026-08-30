"""LLM 客户端 — DeepSeek(OpenAI 兼容)封装。

- 支持文本决策与 JSON 结构化输出(提示词约束 + 容错解析);
- 配置:LLM_API_KEY / LLM_BASE_URL / LLM_MODEL(环境变量或 .env);
- 纯 requests 实现,避免引入 openai 版本兼容问题。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

import requests

from skills.mail.config import load_dotenv

DEFAULT_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-v4-flash"


class LLMError(RuntimeError):
    pass


def _config() -> tuple[str, str, str]:
    load_dotenv()
    key = os.environ.get("LLM_API_KEY", "")
    base = os.environ.get("LLM_BASE_URL", DEFAULT_BASE).rstrip("/")
    model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    return key, base, model


class LLMClient:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 model: Optional[str] = None, timeout: float = 90) -> None:
        env_key, env_base, env_model = _config()
        self.api_key = api_key or env_key
        self.base_url = (base_url or env_base).rstrip("/")
        self.model = model or env_model
        self.timeout = timeout
        if not self.api_key:
            raise LLMError("缺少 LLM_API_KEY,请在 .env 设置(你的 DeepSeek key)")

    def chat(self, messages: list[dict], *, temperature: float = 0.2,
             max_tokens: int = 2048, json_mode: bool = False) -> str:
        """单次对话,返回文本。json_mode=True 时尽量让模型输出 JSON(仍需容错解析)。"""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise LLMError(f"LLM 调用失败 {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        try:
            choice = data["choices"][0]
            message = choice.get("message", {})
            content = message.get("content")
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"LLM 响应结构异常: {data}") from exc
        if not content:
            # DeepSeek v4 是推理模型,reasoning_content 可能占满 max_tokens 导致空输出。
            # 注意:finish_reason 在 choice 层(不在 message 里),读错会导致加倍重试永不触发。
            reason = choice.get("finish_reason", "") or message.get("finish_reason", "")
            if reason == "length" and max_tokens < 8192:
                # 推理占满预算 → 自动加倍重试一次
                return self.chat(messages, temperature=temperature, max_tokens=max_tokens * 2,
                                 json_mode=json_mode)
            raise LLMError(
                f"模型输出为空(finish_reason={reason}, max_tokens={max_tokens})"
            )
        return content

    def chat_json(self, messages: list[dict], **kw) -> dict:
        """对话并要求 JSON 输出,带容错解析(截取 {..} 片段)。"""
        text = self.chat(messages, json_mode=True, **kw)
        return parse_json(text)


def parse_json(text: str) -> dict:
    """容错解析:优先完整 JSON,失败则截取第一个 {..} 平衡片段。"""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 截取从第一个 { 到最后一个 } 的片段
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    # 去代码块围栏后重试
    cleaned = re.sub(r"```(?:json)?", "", text).strip("` \n")
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMError(f"LLM 输出不是合法 JSON: {text[:300]!r}") from exc
