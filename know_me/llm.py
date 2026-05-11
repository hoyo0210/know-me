"""
OpenAI Chat Completions 兼容客户端（LM Studio / llama-server / 多数云端网关）。

在架构中的位置（E02）：
- `embeddings.py` 负责把**文本**变成**向量**（同一向量空间内做相似度）。
- 本模块负责把**带系统提示的多轮消息**发给**对话模型**，得到自然语言答案。

流式（默认 CLI 使用）：
- `POST /v1/chat/completions` 且 JSON 中带 `"stream": true` 时，响应体为 **SSE**：
  多行 `data: {...}`，直至 `data: [DONE]`。
- `iter_chat_complete` 逐段产出 `choices[0].delta.content` 非空片段（兼容常见 OpenAI 兼容实现）。

与 `embeddings.py` 的关系：
- 共用 `KNOW_ME_OPENAI_BASE_URL`（须以 `/v1` 结尾）与 `KNOW_ME_OPENAI_API_KEY`。
- **模型名不同**：嵌入用 `KNOW_ME_OPENAI_EMBED_MODEL`，对话用 `KNOW_ME_OPENAI_CHAT_MODEL`（LM Studio 里常是两个不同模型）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Iterator

import httpx

log = logging.getLogger(__name__)

# OpenAI Chat message：除 role/content 外可含 tool_calls、tool_call_id、name 等
ChatMessage = dict[str, Any]


def _usage_dict(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict) and raw:
        return dict(raw)
    return None


@dataclass(frozen=True)
class ChatCompletionResult:
    """非流式 chat.completions：正文 + 可选 token 用量（网关支持时）。"""

    text: str
    usage: dict[str, Any] | None


@dataclass(frozen=True)
class AssistantTurn:
    """非流式 chat.completions 单条 assistant 消息解析结果。"""

    content: str | None
    tool_calls: list[dict[str, Any]] | None
    usage: dict[str, Any] | None = None


def chat_complete(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[ChatMessage],
    temperature: float = 0.2,
    timeout: float = 120.0,
) -> ChatCompletionResult:
    """
    非流式：返回正文与可选 `usage`（`--no-stream` / 脚本聚合时使用）。
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
        "stream": False,
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
    usage = _usage_dict(data.get("usage"))
    log.debug("chat_complete 用量：%s", usage)
    return ChatCompletionResult(text=content, usage=usage)


def chat_complete_with_tools(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[ChatMessage],
    tools: list[dict[str, Any]],
    temperature: float = 0.2,
    timeout: float = 120.0,
) -> AssistantTurn:
    """
    非流式 + tools：解析 assistant 的 `content` 与 `tool_calls`（供 Agent 多步调用）。
    若服务端不支持 tools，可能只返回 content。
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
        "stream": False,
        "tools": tools,
        "tool_choice": "auto",
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
    content_str = content.strip() if isinstance(content, str) and content.strip() else None
    raw_calls = msg.get("tool_calls")
    tool_calls: list[dict[str, Any]] | None = None
    if isinstance(raw_calls, list) and raw_calls:
        tool_calls = []
        for tc in raw_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function")
            if not isinstance(fn, dict):
                continue
            name = fn.get("name")
            arguments = fn.get("arguments", "")
            if not isinstance(name, str):
                continue
            tid = tc.get("id")
            tool_calls.append(
                {
                    "id": str(tid) if tid is not None else "",
                    "type": tc.get("type") or "function",
                    "function": {"name": name, "arguments": arguments if isinstance(arguments, str) else ""},
                },
            )
        if not tool_calls:
            tool_calls = None
    usage = _usage_dict(data.get("usage"))
    log.debug("chat_complete_with_tools 用量：%s", usage)
    return AssistantTurn(content=content_str, tool_calls=tool_calls, usage=usage)


def iter_chat_complete(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[ChatMessage],
    temperature: float = 0.2,
    timeout: float = 120.0,
) -> Iterator[str]:
    """
    流式：按 SSE 增量产出 assistant 的文本片段（拼接后与 `chat_complete` 全文等价，具体视服务端实现）。

    忽略不含 `delta.content` 的事件（如仅含 `role` 的首包）；遇 `data: [DONE]` 结束。
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
        "stream": True,
    }
    with httpx.Client(timeout=timeout) as client:
        with client.stream("POST", url, headers=headers, json=payload) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                if line.startswith(":"):
                    # SSE 注释/心跳行
                    continue
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    obj: Any = json.loads(data_str)
                except json.JSONDecodeError:
                    log.debug("跳过无法解析的 SSE 行：%s", data_str[:200])
                    continue
                choices = obj.get("choices")
                if not isinstance(choices, list) or not choices:
                    continue
                delta = choices[0].get("delta") or {}
                piece = delta.get("content")
                if isinstance(piece, str) and piece:
                    yield piece
