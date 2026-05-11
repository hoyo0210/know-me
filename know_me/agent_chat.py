"""
E03 — Agent 编排：工具循环 + 最终流式输出（KM-302 / KM-303）。

流程概要：
1. 组装 system + 会话历史 + 本轮 user。
2. 非流式 `chat_complete_with_tools` 循环直至无 `tool_calls` 或达到轮次上限。
3. 执行 `search_personal_knowledge` → `retrieve` + 片段文本回注；`ask_user_clarify` → SSE 事件 + 简短 JSON 回注。
4. 无工具调用后：**始终** `iter_chat_complete` 向 LLM 拉取真实 token 流（`delta` 与网关 SSE 同步）；若流式正文为空再回退到本轮非流式 `content`。
5. 产出统一事件 dict，供 HTTP 层编码为 SSE。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any, Iterator

from know_me.llm import chat_complete_with_tools, iter_chat_complete
from know_me.prompts_agent import AGENT_SYSTEM_PROMPT, AGENT_TOOLS
from know_me.retrieval import citation_dicts_from_chunks, retrieve, retrieved_to_citation_block
from know_me.settings import IndexSettings
from know_me.trace_log import emit_structured_trace

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 10


def _history_messages(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in history:
        role = m.get("role")
        content = m.get("content")
        if role in ("user", "assistant") and isinstance(content, str):
            out.append({"role": role, "content": content})
    return out


def iter_agent_chat_events(
    settings: IndexSettings,
    history: list[dict[str, Any]],
    user_message: str,
    *,
    top_k: int | None = None,
    message_id: str | None = None,
) -> Iterator[dict[str, Any]]:
    """
    产出供 SSE 序列化的事件 dict。

    关键字段：
    - `type=session`：分配到的 session_id（由上层注入也可）
    - `type=delta`：`text` 增量
    - `type=citations`：`items` 为引用表（检索工具累计）
    - `type=clarify`：`question` 澄清问句
    - `type=status`：`message` 给人看的进度提示（首个 token 前、检索前等，避免界面长时间无反馈）
    - `type=done`：最终 `answer`、可选 `clarify`、`message_id`（E05 反馈关联）
    - `type=error`：不可恢复错误说明
    """
    k = top_k if top_k is not None else settings.rag_top_k
    mid = message_id or uuid.uuid4().hex
    t0 = time.perf_counter()
    usages_accum: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = [{"role": "system", "content": AGENT_SYSTEM_PROMPT}]
    messages.extend(_history_messages(history))
    messages.append({"role": "user", "content": user_message.strip()})

    collected_citations: list[dict[str, Any]] = []
    last_clarify: str | None = None

    yield {
        "type": "status",
        "phase": "start",
        "message": "正在连接模型并处理请求（工具推理可能需数秒）…",
    }

    for _ in range(MAX_TOOL_ROUNDS):
        turn = chat_complete_with_tools(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            model=settings.openai_chat_model,
            messages=messages,
            tools=AGENT_TOOLS,
            temperature=settings.llm_temperature,
        )
        if turn.usage:
            usages_accum.append(turn.usage)

        if turn.tool_calls:
            assistant_tool_msg: dict[str, Any] = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": tc.get("type") or "function",
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"],
                        },
                    }
                    for tc in turn.tool_calls
                ],
            }
            if turn.content:
                assistant_tool_msg["content"] = turn.content
            messages.append(assistant_tool_msg)

            for tc in turn.tool_calls:
                tid = str(tc.get("id") or "")
                fn = tc.get("function") or {}
                name = fn.get("name", "") if isinstance(fn, dict) else ""
                raw_args = fn.get("arguments", "") if isinstance(fn, dict) else ""
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else {}
                except json.JSONDecodeError:
                    args = {}
                if not isinstance(args, dict):
                    args = {}

                if name == "search_personal_knowledge":
                    q = str(args.get("query", "")).strip() or user_message.strip()
                    yield {"type": "status", "phase": "retrieve", "message": "正在检索个人知识库…"}
                    chunks = retrieve(settings, q, top_k=k)
                    collected_citations = citation_dicts_from_chunks(chunks)
                    body = (
                        "（未检索到相关片段）"
                        if not chunks
                        else retrieved_to_citation_block(chunks)
                    )
                    messages.append({"role": "tool", "tool_call_id": tid, "content": body})
                elif name == "ask_user_clarify":
                    qn = str(args.get("question", "")).strip() or "能否补充一下具体场景？"
                    last_clarify = qn
                    yield {"type": "clarify", "question": qn}
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tid,
                            "content": json.dumps(
                                {"status": "clarify_asked", "question": qn},
                                ensure_ascii=False,
                            ),
                        },
                    )
                else:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tid,
                            "content": f"未知工具：{name}",
                        },
                    )
            continue

        yield {"type": "citations", "items": collected_citations}

        yield {"type": "status", "phase": "stream", "message": "正在流式生成回答…"}

        buf: list[str] = []
        for frag in iter_chat_complete(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            model=settings.openai_chat_model,
            messages=messages,
            temperature=settings.llm_temperature,
        ):
            buf.append(frag)
            yield {"type": "delta", "text": frag}
        full = "".join(buf).strip()
        if not full and turn.content and turn.content.strip():
            full = turn.content.strip()
        if not full:
            full = "（模型未返回有效正文。）"
        disc = settings.disclaimer_text.strip() or None
        chunk_ids = [
            str(x.get("chunk_id"))
            for x in collected_citations
            if isinstance(x, dict) and x.get("chunk_id") is not None
        ]
        emit_structured_trace(
            settings,
            {
                "event": "agent_chat",
                "message_id": mid,
                "user_message": user_message.strip()[:2000],
                "chunk_ids": chunk_ids,
                "embed_model": settings.openai_embed_model,
                "chat_model": settings.openai_chat_model,
                "latency_total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
                "usage_tool_rounds": usages_accum or None,
            },
        )
        yield {
            "type": "done",
            "answer": full,
            "clarify": last_clarify,
            "disclaimer": disc,
            "message_id": mid,
        }
        return

    emit_structured_trace(
        settings,
        {
            "event": "agent_chat_error",
            "message_id": mid,
            "user_message": user_message.strip()[:2000],
            "error": "工具调用轮次超过上限，请简化问题后重试。",
            "latency_total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
            "usage_tool_rounds": usages_accum or None,
        },
    )
    yield {"type": "error", "message": "工具调用轮次超过上限，请简化问题后重试。", "message_id": mid}


def run_agent_chat_blocking(
    settings: IndexSettings,
    history: list[dict[str, Any]],
    user_message: str,
    *,
    top_k: int | None = None,
    message_id: str | None = None,
    on_status: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """非流式：聚合 `iter_agent_chat_events` 为单份 JSON 友好结构。可选 `on_status` 用于终端进度提示。"""
    foot = settings.disclaimer_text.strip() or None
    deltas: list[str] = []
    citations: list[dict[str, Any]] = []
    clarify: str | None = None
    answer = ""
    err: str | None = None
    for ev in iter_agent_chat_events(settings, history, user_message, top_k=top_k, message_id=message_id):
        t = ev.get("type")
        if t == "status" and on_status is not None:
            on_status(ev)
        elif t == "delta" and isinstance(ev.get("text"), str):
            deltas.append(ev["text"])
        elif t == "citations" and isinstance(ev.get("items"), list):
            citations = list(ev["items"])
        elif t == "clarify" and isinstance(ev.get("question"), str):
            clarify = ev["question"]
        elif t == "done":
            answer = str(ev.get("answer") or "".join(deltas)).strip()
            if ev.get("clarify"):
                clarify = str(ev["clarify"])
            disc = ev.get("disclaimer")
            if not isinstance(disc, str) or not disc.strip():
                disc = foot
            else:
                disc = disc.strip() or None
            return {
                "answer": answer,
                "citations": citations,
                "clarify": clarify,
                "disclaimer": disc,
                "message_id": str(ev.get("message_id") or ""),
            }
        elif t == "error":
            err = str(ev.get("message") or "error")
            return {
                "answer": "".join(deltas).strip(),
                "citations": citations,
                "clarify": clarify,
                "disclaimer": foot,
                "error": err,
                "message_id": str(ev.get("message_id") or ""),
            }
    return {
        "answer": answer or "".join(deltas).strip(),
        "citations": citations,
        "clarify": clarify,
        "disclaimer": foot,
        "error": err,
        "message_id": "",
    }
