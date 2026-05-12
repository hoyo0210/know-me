"""
E03 — Agent 编排：工具循环 + 最终流式输出（KM-302 / KM-303）。

流程概要：
0. **新会话**：开场白由 `POST /session` 或 SSE 首包 `session.opening` 下发（不经 LLM）；`iter_agent_chat_events` 仅把开场注入 LLM 上下文。`preface_shown=True` 表示客户端已展示过开场，不再重复推送。
0b. **非新会话的纯寒暄**：本地固定短句，不跑工具首轮整包推理。
1. 组装 **单条** `system`（新会话时把已展示的开场白并入 system，避免 `assistant` 夹在首条 `user` 前触发部分网关 400）+ 会话历史 + 本轮 `user`。
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

from know_me.job_intent import (
    greeting_fast_answer,
    is_greeting_only_message,
    should_retrieve_personal_corpus,
)
from know_me.llm import chat_complete_with_tools, iter_chat_complete
from know_me.prompts_agent import AGENT_SYSTEM_PROMPT, AGENT_TOOLS, SESSION_OPENING_ASK_IDENTITY
from know_me.retrieval import citation_dicts_from_chunks, retrieve, retrieved_to_citation_block
from know_me.settings import IndexSettings
from know_me.trace_log import emit_structured_trace

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 10
_AGENT_TOOLS_JSON_CHARS = len(json.dumps(AGENT_TOOLS, ensure_ascii=False))
_MAX_USER_MSG_CHARS = 8000

def _approx_chat_payload_chars(messages: list[dict[str, Any]], tools_chars: int) -> int:
    """粗估发往 chat.completions 的 messages + tools JSON 字符量（与网关 token 限制单调相关）。"""
    return len(json.dumps(messages, ensure_ascii=False)) + tools_chars


def _trim_hist_and_user_for_budget(
    hist_sl: list[dict[str, Any]],
    *,
    system_content: str,
    user_content: str,
    char_budget: int,
) -> tuple[list[dict[str, Any]], str]:
    """从最早一轮丢弃 user+assistant；仍超则缩短用户正文，避免本地网关 context 溢出。"""
    h = list(hist_sl)
    u = user_content if len(user_content) <= _MAX_USER_MSG_CHARS else user_content[:_MAX_USER_MSG_CHARS].rstrip() + "\n（…上文已截断）"
    limit = max(int(char_budget), 4096)

    def over() -> bool:
        msgs = [{"role": "system", "content": system_content}, *h, {"role": "user", "content": u}]
        return _approx_chat_payload_chars(msgs, _AGENT_TOOLS_JSON_CHARS) > limit

    dropped = 0
    while len(h) >= 2 and over():
        h = h[2:]
        dropped += 2
    if dropped:
        log.warning(
            "为适配 KNOW_ME_AGENT_CONTEXT_CHAR_BUDGET=%s，已丢弃最早 %d 条历史消息",
            char_budget,
            dropped,
        )
    u_truncated = False
    guard = 0
    while over() and len(u) > 400 and guard < 48:
        u = u[: int(len(u) * 0.82)].rstrip() + "\n（…已截断）"
        u_truncated = True
        guard += 1
    if u_truncated:
        log.warning("用户本轮输入已缩短以适配 KNOW_ME_AGENT_CONTEXT_CHAR_BUDGET=%s", char_budget)
    if over():
        log.error(
            "在 KNOW_ME_AGENT_CONTEXT_CHAR_BUDGET=%s 下仍无法容纳 system+tools+本轮；请调大该变量或换更大上下文的对话模型",
            char_budget,
        )
    return h, u


def _clamp_tool_body(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return (
        text[:max_chars].rstrip()
        + "\n\n（检索片段过长，已截断；请仅依据已给出内容回答，勿编造未出现的细节。）"
    )


# 部分 OpenAI 兼容服务要求 system 后首条须为 user，不接受「system → assistant(开场) → user」。
_SESSION_OPENING_IN_SYSTEM_NOTE = (
    "\n\n【上下文：以下为已对用户展示的开场白原文，勿逐字重复；请直接针对用户本轮输入作答。】\n"
)


def _done_event(
    *,
    answer: str,
    answer_stored: str | None,
    clarify: str | None,
    disclaimer: str | None,
    message_id: str,
) -> dict[str, Any]:
    """`answer` 给当前 UI；`answer_stored` 在已与界面展示去重时写入会话历史（与 answer 不同才下发）。"""
    ev: dict[str, Any] = {
        "type": "done",
        "answer": answer,
        "clarify": clarify,
        "disclaimer": disclaimer,
        "message_id": message_id,
    }
    if answer_stored is not None and answer_stored.strip() and answer_stored.strip() != answer.strip():
        ev["answer_stored"] = answer_stored.strip()
    return ev


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
    preface_shown: bool = False,
) -> Iterator[dict[str, Any]]:
    """
    产出供 SSE 序列化的事件 dict。

    关键字段：
    - `type=session`：分配到的 session_id；可选 `opening`（新会话且客户端未先调 `/session` 时由网关附带）
    - `type=delta`：`text` 增量
    - `type=citations`：`items` 为引用表（检索工具累计）
    - `type=clarify`：`question` 澄清问句
    - `type=status`：`message` 给人看的进度提示（首个 token 前、检索前等，避免界面长时间无反馈）
    - `type=done`：最终 `answer`（当前气泡展示）；若与入库全文不同则另含 `answer_stored`（供服务端 `append_turn`，避免 Web 已单独展示开场时重复）
    - `type=error`：不可恢复错误说明
    """
    k = top_k if top_k is not None else settings.rag_top_k
    mid = message_id or uuid.uuid4().hex
    t0 = time.perf_counter()
    usages_accum: list[dict[str, Any]] = []
    um = user_message.strip()
    hist_sl = _history_messages(history)
    hist_empty = len(hist_sl) == 0
    session_prefix: str | None = None

    # 新会话：开场仅写入 LLM 上下文；未 preface 时由 HTTP 层在 session 事件中附带 opening
    if hist_empty:
        session_prefix = SESSION_OPENING_ASK_IDENTITY
        log.info("新会话：开场已注入上下文；preface_shown=%s（开场由网关 /session 或 session.opening 下发，此处不重复 delta）", preface_shown)
        if is_greeting_only_message(um):
            tail = "收到～您请讲具体问题哈。"
            yield {"type": "citations", "items": []}
            yield {"type": "delta", "text": tail if preface_shown else ("\n\n" + tail)}
            stored = f"{session_prefix}\n\n{tail}".strip()
            display = tail if preface_shown else stored
            disc = settings.disclaimer_text.strip() or None
            emit_structured_trace(
                settings,
                {
                    "event": "agent_chat",
                    "message_id": mid,
                    "user_message": um[:2000],
                    "chunk_ids": [],
                    "embed_model": settings.openai_embed_model,
                    "chat_model": settings.openai_chat_model,
                    "latency_total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
                    "usage_tool_rounds": None,
                    "path": "session_open_greeting_local",
                },
            )
            yield _done_event(
                answer=display,
                answer_stored=stored,
                clarify=None,
                disclaimer=disc,
                message_id=mid,
            )
            return

    # 会话已进行中：纯寒暄仍走本地秒回
    if is_greeting_only_message(um):
        log.info("纯寒暄：本地即时回复，跳过模型与工具")
        full = greeting_fast_answer(um)
        yield {"type": "citations", "items": []}
        yield {"type": "delta", "text": full}
        disc = settings.disclaimer_text.strip() or None
        emit_structured_trace(
            settings,
            {
                "event": "agent_chat",
                "message_id": mid,
                "user_message": um[:2000],
                "chunk_ids": [],
                "embed_model": settings.openai_embed_model,
                "chat_model": settings.openai_chat_model,
                "latency_total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
                "usage_tool_rounds": None,
                "path": "greeting_local",
            },
        )
        yield {
            "type": "done",
            "answer": full,
            "clarify": None,
            "disclaimer": disc,
            "message_id": mid,
        }
        return

    messages: list[dict[str, Any]]
    system_body = AGENT_SYSTEM_PROMPT
    if session_prefix:
        system_body = AGENT_SYSTEM_PROMPT + _SESSION_OPENING_IN_SYSTEM_NOTE + session_prefix
    hist_for_llm, um_llm = _trim_hist_and_user_for_budget(
        hist_sl,
        system_content=system_body,
        user_content=um,
        char_budget=settings.agent_context_char_budget,
    )
    messages = [{"role": "system", "content": system_body}]
    messages.extend(hist_for_llm)
    messages.append({"role": "user", "content": um_llm})

    if _approx_chat_payload_chars(messages, _AGENT_TOOLS_JSON_CHARS) > settings.agent_context_char_budget:
        yield {
            "type": "error",
            "message": (
                f"当前请求仍超过上下文预算（KNOW_ME_AGENT_CONTEXT_CHAR_BUDGET={settings.agent_context_char_budget}）。"
                "请调大该环境变量或换更大上下文的对话模型。"
            ),
            "message_id": mid,
        }
        return

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
                    if not should_retrieve_personal_corpus(user_message.strip()):
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tid,
                                "content": (
                                    "（系统：用户本轮已判定为非 HR 初筛 / 非求职相关信息，已跳过个人知识库检索。"
                                    "请用 2～4 条极短口语引导对方改为岗位、履历、初筛相关问题；勿编造个人事实。）"
                                ),
                            },
                        )
                        collected_citations = []
                        continue
                    yield {"type": "status", "phase": "retrieve", "message": "正在检索个人知识库…"}
                    chunks = retrieve(settings, q, top_k=k)
                    collected_citations = citation_dicts_from_chunks(chunks)
                    body = (
                        "（未检索到相关片段）"
                        if not chunks
                        else _clamp_tool_body(
                            retrieved_to_citation_block(chunks),
                            settings.agent_tool_result_max_chars,
                        )
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
        body = "".join(buf).strip()
        if not body and turn.content and turn.content.strip():
            body = turn.content.strip()
        if not body:
            body = "（模型未返回有效正文。）"
        if session_prefix:
            stored = f"{session_prefix}\n\n{body}".strip()
            display = body if preface_shown else stored
        else:
            stored = body
            display = body
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
        yield _done_event(
            answer=display,
            answer_stored=stored,
            clarify=last_clarify,
            disclaimer=disc,
            message_id=mid,
        )
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
    preface_shown: bool = False,
) -> dict[str, Any]:
    """非流式：聚合 `iter_agent_chat_events` 为单份 JSON 友好结构。可选 `on_status` 用于终端进度提示。"""
    foot = settings.disclaimer_text.strip() or None
    deltas: list[str] = []
    citations: list[dict[str, Any]] = []
    clarify: str | None = None
    answer = ""
    err: str | None = None
    for ev in iter_agent_chat_events(
        settings,
        history,
        user_message,
        top_k=top_k,
        message_id=message_id,
        preface_shown=preface_shown,
    ):
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
            stored = ev.get("answer_stored")
            if isinstance(stored, str) and stored.strip():
                stored = stored.strip()
            else:
                stored = answer
            if ev.get("clarify"):
                clarify = str(ev["clarify"])
            disc = ev.get("disclaimer")
            if not isinstance(disc, str) or not disc.strip():
                disc = foot
            else:
                disc = disc.strip() or None
            out: dict[str, Any] = {
                "answer": answer,
                "citations": citations,
                "clarify": clarify,
                "disclaimer": disc,
                "message_id": str(ev.get("message_id") or ""),
            }
            if stored != answer:
                out["answer_stored"] = stored
            return out
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
