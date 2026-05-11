"""
OpenAI Chat Completions 兼容客户端（LM Studio / llama-server / 多数云端网关）。

与 `embeddings.py` 共用同一 `KNOW_ME_OPENAI_BASE_URL`（/v1 根路径），仅路径改为 `/chat/completions`。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


def chat_complete(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.2,
    timeout: float = 120.0,
) -> str:
    """
    调用 `POST {base_url}/chat/completions`，返回 assistant 文本内容。

    `messages` 为 OpenAI 格式：`[{"role":"system","content":"..."}, ...]`。
    """
    if not model.strip():
        raise ValueError("未配置对话模型名（KNOW_ME_OPENAI_CHAT_MODEL）")
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload: dict[str, Any] = {
        "model": model.strip(),
        "messages": messages,
        "temperature": temperature,
    }
    with httpx.Client(timeout=timeout) as client:
        r = client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError(f"对话接口返回异常：{data!r}")
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    if not isinstance(content, str):
        raise RuntimeError(f"对话接口未返回文本 content：{choices[0]!r}")
    log.debug("chat_complete 用量：%s", data.get("usage"))
    return content
