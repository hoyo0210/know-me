"""
OpenAI Chat Completions 兼容对话（LM Studio / llama-server / 多数云端网关）。

实现方式：**LangChain** `langchain_openai.ChatOpenAI`（Runnable / LCEL 可组合）+ `langchain_core.messages`，
替代原先手写 `httpx` 调 `/v1/chat/completions`，对外仍暴露本模块原有的函数签名，供 RAG 与 Agent 无感调用。

- `embeddings` 仍走自建 `httpx`（见 `know_me.index.embeddings`）；本文件仅负责 **chat**。
- 流式：`ChatOpenAI.stream` 产出 `AIMessageChunk`，逐段拼接等价于原 `iter_chat_complete`。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Iterator

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

log = logging.getLogger(__name__)

# OpenAI Chat message：除 role/content 外可含 tool_calls、tool_call_id、name 等
ChatMessage = dict[str, Any]


def _api_key_effective(api_key: str) -> str:
    """无鉴权本地网关常留空；OpenAI SDK 要求非空 api_key，故用占位符（多数兼容服务会忽略 Authorization）。"""
    s = (api_key or "").strip()
    return s if s else "not-needed"


def _usage_dict_from_message(msg: BaseMessage) -> dict[str, Any] | None:
    um = getattr(msg, "usage_metadata", None)
    if um is not None:
        if isinstance(um, dict):
            d = {k: v for k, v in um.items() if v is not None}
            return d if d else None
        inp = getattr(um, "input_tokens", None)
        out = getattr(um, "output_tokens", None)
        tot = getattr(um, "total_tokens", None)
        if inp is None and out is None and tot is None:
            return None
        return {
            "prompt_tokens": inp,
            "completion_tokens": out,
            "total_tokens": tot,
        }
    meta = getattr(msg, "response_metadata", None) or {}
    tu = meta.get("token_usage")
    if isinstance(tu, dict) and tu:
        return dict(tu)
    return None


def _str_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text")
                if isinstance(t, str):
                    parts.append(t)
        return "".join(parts)
    return str(content)


def _flatten_message_content(content: Any) -> str:
    return _str_content(content).strip()


def normalize_messages_for_chat_gateway(messages: list[ChatMessage]) -> list[ChatMessage]:
    """压平 content、补齐 assistant(tool) 的 content，并保证末尾为非空 user。"""
    out: list[ChatMessage] = []
    for m in messages:
        role = m.get("role")
        if role not in ("system", "user", "assistant", "tool"):
            continue
        flat = _flatten_message_content(m.get("content"))
        item: ChatMessage = {"role": role, "content": flat}
        if role == "assistant":
            tc = m.get("tool_calls")
            if isinstance(tc, list) and tc:
                item["tool_calls"] = tc
                if not flat:
                    item["content"] = ""
        elif role == "tool":
            tid = m.get("tool_call_id")
            if tid is not None:
                item["tool_call_id"] = tid
            if not flat:
                item["content"] = "(无结果)"
        out.append(item)

    if not out:
        out.append({"role": "user", "content": "请继续。"})
        return out

    if not any(m.get("role") == "user" and str(m.get("content") or "").strip() for m in out):
        out.append({"role": "user", "content": "请继续。"})

    if out[-1].get("role") != "user":
        last_user = ""
        for m in reversed(out):
            if m.get("role") == "user" and str(m.get("content") or "").strip():
                last_user = str(m["content"]).strip()
                break
        out.append({"role": "user", "content": last_user or "请继续。"})
    elif not str(out[-1].get("content") or "").strip():
        out[-1]["content"] = "请继续。"

    return out


def should_lmstudio_qwen_compat(*, model: str, base_url: str) -> bool:
    """
    LM Studio 上 Qwen3.5+ 在请求带 tools 且 messages 含多轮 user 时，
    默认 Jinja 常报 No user query found；压成 system+单 user 可规避。
    """
    raw = os.environ.get("KNOW_ME_LMSTUDIO_QWEN_COMPAT", "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    m = (model or "").lower()
    u = (base_url or "").lower()
    if "qwen" not in m:
        return False
    return (
        "127.0.0.1" in u
        or "localhost" in u
        or "192.168." in u
        or ":9999" in u
        or ":1234" in u
    )


def collapse_history_for_lmstudio_qwen(messages: list[ChatMessage]) -> list[ChatMessage]:
    """将多轮 user/assistant/tool 并入 system，仅保留最后一条 user 供模板识别。"""
    system_parts: list[str] = []
    transcript_lines: list[str] = []
    user_contents: list[str] = []

    for m in messages:
        role = m.get("role")
        content = _flatten_message_content(m.get("content"))
        if role == "system":
            if content:
                system_parts.append(content)
        elif role == "user":
            user_contents.append(content)
            if content:
                transcript_lines.append(f"招聘方：{content}")
        elif role == "assistant":
            if isinstance(m.get("tool_calls"), list) and m.get("tool_calls"):
                transcript_lines.append("助手：（请求调用工具）")
            elif content:
                transcript_lines.append(f"助手：{content}")
        elif role == "tool" and content:
            body = content if len(content) <= 6000 else content[:6000].rstrip() + "\n（…已截断）"
            transcript_lines.append(f"工具回注：{body}")

    if transcript_lines:
        system_parts.append(
            "\n\n【此前对话与工具回注 · 仅供续答】\n" + "\n".join(transcript_lines),
        )

    last_user = ""
    for u in reversed(user_contents):
        if u.strip():
            last_user = u.strip()
            break
    if not last_user:
        last_user = "请继续。"

    out: list[ChatMessage] = []
    if system_parts:
        out.append({"role": "system", "content": "\n\n".join(system_parts)})
    out.append({"role": "user", "content": last_user})
    return out


def prepare_agent_input_for_langgraph(
    *,
    system_content: str,
    history: list[ChatMessage],
    user_content: str,
    model: str,
    base_url: str,
) -> tuple[str, list[BaseMessage]]:
    """
    供 LangChain `create_agent` 使用：返回 (system_prompt, 不含 system 的 messages)。
    含 LM Studio / Qwen 兼容折叠。
    """
    dict_msgs: list[ChatMessage] = []
    for m in history:
        role = m.get("role")
        if role in ("user", "assistant"):
            dict_msgs.append({"role": role, "content": m.get("content", "")})
    dict_msgs.append({"role": "user", "content": user_content})
    packed: list[ChatMessage] = [{"role": "system", "content": system_content}, *dict_msgs]
    api = prepare_messages_for_chat_api(packed, model=model, base_url=base_url)
    sys_out = system_content
    rest: list[ChatMessage] = []
    for m in api:
        if m.get("role") == "system":
            sys_out = str(m.get("content") or system_content)
        else:
            rest.append(m)
    return sys_out, _dict_messages_to_lc(rest)


def prepare_messages_for_chat_api(
    messages: list[ChatMessage],
    *,
    model: str,
    base_url: str,
) -> list[ChatMessage]:
    msgs = normalize_messages_for_chat_gateway(messages)
    if should_lmstudio_qwen_compat(model=model, base_url=base_url):
        msgs = collapse_history_for_lmstudio_qwen(msgs)
        log.debug(
            "LM Studio Qwen 兼容：已折叠为 system+单 user（roles=%s）",
            [m.get("role") for m in msgs],
        )
    return msgs


def _dict_messages_to_lc(messages: list[ChatMessage]) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for m in messages:
        role = m.get("role")
        content_raw = m.get("content")
        if role == "system":
            out.append(SystemMessage(content=_str_content(content_raw)))
        elif role == "user":
            out.append(HumanMessage(content=_str_content(content_raw)))
        elif role == "assistant":
            tc_raw = m.get("tool_calls")
            if isinstance(tc_raw, list) and tc_raw:
                tool_calls: list[dict[str, Any]] = []
                for tc in tc_raw:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function")
                    if not isinstance(fn, dict):
                        continue
                    name = fn.get("name")
                    if not isinstance(name, str):
                        continue
                    args_raw = fn.get("arguments", "{}")
                    if isinstance(args_raw, str):
                        try:
                            args_obj: Any = json.loads(args_raw) if args_raw.strip() else {}
                        except json.JSONDecodeError:
                            args_obj = {}
                    elif isinstance(args_raw, dict):
                        args_obj = args_raw
                    else:
                        args_obj = {}
                    tid = tc.get("id")
                    tool_calls.append(
                        {
                            "name": name,
                            "args": args_obj if isinstance(args_obj, dict) else {},
                            "id": str(tid) if tid is not None else "",
                            "type": "tool_call",
                        },
                    )
                out.append(AIMessage(content=_str_content(content_raw), tool_calls=tool_calls))
            else:
                out.append(AIMessage(content=_str_content(content_raw)))
        elif role == "tool":
            tid = m.get("tool_call_id")
            out.append(
                ToolMessage(
                    content=_str_content(content_raw),
                    tool_call_id=str(tid) if tid is not None else "",
                ),
            )
        else:
            log.debug("跳过未知 role 的消息：%s", role)
    return out


def _lc_tool_calls_to_openai(raw: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not raw:
        return None
    tool_calls: list[dict[str, Any]] = []
    for tc in raw:
        if not isinstance(tc, dict):
            continue
        name = tc.get("name")
        if not isinstance(name, str):
            continue
        tid = tc.get("id", "")
        args = tc.get("args")
        if args is None and isinstance(tc.get("arguments"), str):
            try:
                args = json.loads(tc["arguments"]) if tc["arguments"].strip() else {}
            except (json.JSONDecodeError, TypeError, AttributeError):
                args = {}
        if not isinstance(args, dict):
            args = {}
        tool_calls.append(
            {
                "id": str(tid) if tid is not None else "",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args, ensure_ascii=False),
                },
            },
        )
    return tool_calls or None


def _chat_model(
    *,
    base_url: str,
    api_key: str,
    model: str,
    temperature: float,
    timeout: float,
) -> ChatOpenAI:
    return ChatOpenAI(
        model=model.strip(),
        base_url=base_url.rstrip("/"),
        api_key=_api_key_effective(api_key),
        temperature=temperature,
        timeout=timeout,
    )


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
    llm = _chat_model(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=temperature,
        timeout=timeout,
    )
    api_messages = prepare_messages_for_chat_api(
        messages, model=model, base_url=base_url,
    )
    lc_messages = _dict_messages_to_lc(api_messages)
    try:
        resp = llm.invoke(lc_messages)
    except Exception as e:
        log.warning("chat_complete 调用失败：%s", e)
        raise
    if not isinstance(resp, AIMessage):
        raise RuntimeError(f"对话接口返回非 AIMessage：{type(resp)!r}")
    text = _str_content(resp.content).strip()
    if not text:
        raise RuntimeError(f"对话接口未返回文本 content：{resp!r}")
    usage = _usage_dict_from_message(resp)
    log.debug("chat_complete 用量：%s", usage)
    return ChatCompletionResult(text=text, usage=usage)


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
    llm = _chat_model(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=temperature,
        timeout=timeout,
    )
    bound = llm.bind_tools(tools, tool_choice="auto")
    api_messages = prepare_messages_for_chat_api(
        messages, model=model, base_url=base_url,
    )
    lc_messages = _dict_messages_to_lc(api_messages)
    try:
        resp = bound.invoke(lc_messages)
    except Exception as e:
        try:
            log.warning(
                "chat_complete_with_tools 调用失败：%s",
                e,
            )
        except Exception:
            pass
        raise
    if not isinstance(resp, AIMessage):
        raise RuntimeError(f"对话接口返回非 AIMessage：{type(resp)!r}")
    content_str = _str_content(resp.content).strip() or None
    tool_calls = _lc_tool_calls_to_openai(resp.tool_calls)
    usage = _usage_dict_from_message(resp)
    log.debug("chat_complete_with_tools 用量：%s", usage)
    return AssistantTurn(content=content_str, tool_calls=tool_calls, usage=usage)


def _yield_text_from_stream_chunk(chunk: BaseMessage) -> Iterator[str]:
    c = getattr(chunk, "content", None)
    if isinstance(c, str) and c:
        yield c
        return
    if isinstance(c, list):
        for block in c:
            if isinstance(block, str) and block:
                yield block
            elif isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text")
                if isinstance(t, str) and t:
                    yield t


def build_chat_openai(
    *,
    base_url: str,
    api_key: str,
    model: str,
    temperature: float = 0.2,
    timeout: float = 120.0,
) -> ChatOpenAI:
    """
    返回已按 OpenAI 兼容网关配置好的 `ChatOpenAI`，便于在业务侧自行拼装 **LCEL / Runnable** 或接入 LangGraph。
    与 `chat_complete` 等函数使用相同的环境语义（`base_url` 须含 `/v1`）。
    """
    if not (model or "").strip():
        raise ValueError("未配置对话模型名（KNOW_ME_OPENAI_CHAT_MODEL）")
    return _chat_model(
        base_url=base_url,
        api_key=api_key,
        model=model.strip(),
        temperature=temperature,
        timeout=timeout,
    )


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
    流式：按模型增量产出 assistant 的文本片段（与原先 SSE 解析行为对齐，由 LangChain 封装传输细节）。
    """
    if not model.strip():
        raise ValueError("未配置对话模型名（KNOW_ME_OPENAI_CHAT_MODEL）")
    llm = _chat_model(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=temperature,
        timeout=timeout,
    )
    api_messages = prepare_messages_for_chat_api(
        messages, model=model, base_url=base_url,
    )
    lc_messages = _dict_messages_to_lc(api_messages)
    try:
        for chunk in llm.stream(lc_messages):
            yield from _yield_text_from_stream_chunk(chunk)
    except Exception as e:
        log.warning("iter_chat_complete 流式调用失败：%s", e)
        raise
