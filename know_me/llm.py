"""
OpenAI Chat Completions 兼容客户端（LM Studio / llama-server / 多数云端网关）。

在架构中的位置（E02）：
- `embeddings.py` 负责把**文本**变成**向量**（同一向量空间内做相似度）。
- 本模块负责把**带系统提示的多轮消息**发给**对话模型**，得到自然语言答案。

与 `embeddings.py` 的关系：
- 共用 `KNOW_ME_OPENAI_BASE_URL`（须以 `/v1` 结尾）与 `KNOW_ME_OPENAI_API_KEY`。
- **模型名不同**：嵌入用 `KNOW_ME_OPENAI_EMBED_MODEL`，对话用 `KNOW_ME_OPENAI_CHAT_MODEL`（LM Studio 里常是两个不同模型）。
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
    调用 `POST {base_url}/chat/completions`，返回**第一条** choice 的 assistant 文本。

    参数说明：
    - messages：OpenAI Chat 格式，通常包含 system（约束）与 user（问题 + 证据块）。
    - temperature：采样温度；RAG 场景宜偏低以增强忠实度（默认 0.2，可用环境变量调）。
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

    # OpenAI 风格：choices[0].message.content 为助手回复正文
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError(f"对话接口返回异常：{data!r}")
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    if not isinstance(content, str):
        raise RuntimeError(f"对话接口未返回文本 content：{choices[0]!r}")
    log.debug("chat_complete 用量：%s", data.get("usage"))
    return content
