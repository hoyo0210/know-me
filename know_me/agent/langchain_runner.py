"""
E03 — LangChain `create_agent`（LangGraph）编排：工具循环 + 流式 SSE 事件。

替代手写 `chat_complete_with_tools` 轮询；检索 / 澄清逻辑仍在 Know Me 工具实现内。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.errors import GraphRecursionError

from know_me.agent.prompts_agent import AGENT_TOOLS
from know_me.core.settings import IndexSettings
from know_me.observability.trace_log import emit_structured_trace
from know_me.rag.job_intent import should_retrieve_personal_corpus
from know_me.rag.llm import (
    _str_content,
    _usage_dict_from_message,
    _yield_text_from_stream_chunk,
    build_chat_openai,
    prepare_agent_input_for_langgraph,
)
from know_me.rag.retrieval import citation_dicts_from_chunks, retrieve, retrieved_to_citation_block

log = logging.getLogger(__name__)


@dataclass
class _AgentRunContext:
    settings: IndexSettings
    user_message: str
    top_k: int
    tool_body_cap: int
    in_fast_window: bool
    citations: list[dict[str, Any]] = field(default_factory=list)
    clarify: str | None = None
    pending_sse: list[dict[str, Any]] = field(default_factory=list)
    usages: list[dict[str, Any]] = field(default_factory=list)
    search_used: bool = False
    last_search_body: str = ""
    retrieve_status_sent: bool = False


def _clamp_tool_body(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n（…已截断）"


def _openai_tool_description(name: str) -> str:
    for spec in AGENT_TOOLS:
        fn = spec.get("function") or {}
        if fn.get("name") == name:
            return str(fn.get("description") or "")
    return ""


def _make_tools(ctx: _AgentRunContext) -> list[Any]:
    def search_personal_knowledge(query: str) -> str:
        um = ctx.user_message.strip()
        if not should_retrieve_personal_corpus(um):
            return (
                "（系统：用户本轮已判定为非 HR 初筛 / 非求职相关信息，已跳过个人知识库检索。"
                "请用简短书面引导对方改为岗位、履历、初筛相关问题；勿编造个人事实。）"
            )
        if ctx.search_used:
            if ctx.last_search_body:
                return (
                    "（系统：本轮已检索过个人知识库，请勿重复调用 search_personal_knowledge。"
                    "请直接根据上一条 tool 回注与 system 规则作答。）\n\n"
                    + ctx.last_search_body
                )
            return (
                "（系统：本轮检索已开始，请根据 tool 回注作答，勿重复调用 search_personal_knowledge。）"
            )
        ctx.search_used = True
        if not ctx.retrieve_status_sent:
            ctx.pending_sse.append(
                {"type": "status", "phase": "retrieve", "message": "正在检索个人知识库…"},
            )
            ctx.retrieve_status_sent = True
        hr_kw: bool | None = (
            False
            if ctx.in_fast_window and ctx.settings.agent_fast_disable_hr_boost
            else None
        )
        q = (query or "").strip() or um
        chunks = retrieve(ctx.settings, q, top_k=ctx.top_k, use_hr_boost=hr_kw)
        ctx.citations = citation_dicts_from_chunks(chunks)
        if not chunks:
            body = "（未检索到相关片段）"
        else:
            body = _clamp_tool_body(
                retrieved_to_citation_block(chunks),
                ctx.tool_body_cap,
            )
        ctx.last_search_body = body
        return body

    def ask_user_clarify(question: str) -> str:
        if ctx.clarify:
            return json.dumps(
                {
                    "status": "clarify_already_asked",
                    "question": ctx.clarify,
                    "hint": "勿再次调用 ask_user_clarify，请等待用户下一条消息。",
                },
                ensure_ascii=False,
            )
        qn = (question or "").strip() or "能否补充一下具体场景？"
        ctx.clarify = qn
        ctx.pending_sse.append({"type": "clarify", "question": qn})
        return json.dumps({"status": "clarify_asked", "question": qn}, ensure_ascii=False)

    return [
        StructuredTool.from_function(
            search_personal_knowledge,
            name="search_personal_knowledge",
            description=_openai_tool_description("search_personal_knowledge"),
        ),
        StructuredTool.from_function(
            ask_user_clarify,
            name="ask_user_clarify",
            description=_openai_tool_description("ask_user_clarify"),
        ),
    ]


def _flush_pending(ctx: _AgentRunContext) -> Iterator[dict[str, Any]]:
    while ctx.pending_sse:
        yield ctx.pending_sse.pop(0)


def _maybe_yield_citations(ctx: _AgentRunContext, *, sent: bool) -> tuple[bool, Iterator[dict[str, Any]]]:
    if sent or not ctx.citations:
        return sent, iter(())
    return True, iter([{"type": "citations", "items": list(ctx.citations)}])


def _message_has_tool_calls(msg: BaseMessage) -> bool:
    if isinstance(msg, AIMessage):
        return bool(msg.tool_calls)
    if isinstance(msg, AIMessageChunk):
        return bool(getattr(msg, "tool_calls", None) or getattr(msg, "tool_call_chunks", None))
    return False


def iter_langchain_agent_events(
    *,
    settings: IndexSettings,
    system_body: str,
    hist_for_llm: list[dict[str, Any]],
    um_llm: str,
    user_message: str,
    top_k: int,
    tool_body_cap: int,
    in_fast_window: bool,
    llm_timeout: float,
    max_tool_rounds: int,
    message_id: str,
    t0: float,
    session_prefix: str | None,
    preface_shown: bool,
    opening_already_in_db: bool,
    done_event_fn: Any,
    reply_display_stored_fn: Any,
) -> Iterator[dict[str, Any]]:
    """
    LangGraph Agent 流式运行，产出与 `iter_agent_chat_events` 兼容的 SSE 事件 dict。
    """
    ctx = _AgentRunContext(
        settings=settings,
        user_message=user_message,
        top_k=top_k,
        tool_body_cap=tool_body_cap,
        in_fast_window=in_fast_window,
    )
    llm = build_chat_openai(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
        model=settings.openai_chat_model,
        temperature=settings.llm_temperature,
        timeout=llm_timeout,
    )
    tools = _make_tools(ctx)
    sys_prompt, lc_messages = prepare_agent_input_for_langgraph(
        system_content=system_body,
        history=hist_for_llm,
        user_content=um_llm,
        model=settings.openai_chat_model,
        base_url=settings.openai_base_url,
    )
    graph = create_agent(model=llm, tools=tools, system_prompt=sys_prompt)
    # LangGraph 每步（含流式 chunk）计入 recursion；下限须能容纳「模型→工具→再生成」
    config = {"recursion_limit": max(16, max(1, max_tool_rounds) * 4 + 8)}

    yield from _flush_pending(ctx)
    yield {
        "type": "status",
        "phase": "start",
        "message": "正在连接模型并思考…",
    }

    citations_sent = False
    stream_status_sent = False
    answer_parts: list[str] = []
    last_ai_text = ""
    saw_tool_message = False
    pre_tool_buf: list[str] = []

    def _yield_clarify_done() -> Iterator[dict[str, Any]]:
        yield from _flush_pending(ctx)
        q = (ctx.clarify or "").strip() or "能否补充一下具体场景？"
        display, stored = reply_display_stored_fn(
            q,
            session_prefix=session_prefix,
            preface_shown=preface_shown,
            opening_already_in_db=opening_already_in_db,
        )
        disc = settings.disclaimer_text.strip() or None
        yield done_event_fn(
            answer=display,
            answer_stored=stored,
            clarify=ctx.clarify,
            disclaimer=disc,
            message_id=message_id,
        )

    def _emit_text_fragments(frags: list[str]) -> Iterator[dict[str, Any]]:
        nonlocal stream_status_sent, citations_sent, last_ai_text
        if not frags:
            return
        if not stream_status_sent:
            citations_sent, evs = _maybe_yield_citations(ctx, sent=citations_sent)
            for ev in evs:
                yield ev
            yield {
                "type": "status",
                "phase": "stream",
                "message": "正在生成回答…",
            }
            stream_status_sent = True
        for frag in frags:
            answer_parts.append(frag)
            last_ai_text = "".join(answer_parts)
            yield {"type": "delta", "text": frag}

    try:
        for item in graph.stream(
            {"messages": lc_messages},
            stream_mode="messages",
            config=config,
        ):
            if isinstance(item, tuple) and len(item) == 2:
                msg, _meta = item
            else:
                msg = item
                _meta = {}

            yield from _flush_pending(ctx)

            if isinstance(msg, ToolMessage):
                pre_tool_buf.clear()
                saw_tool_message = True
                name = getattr(msg, "name", None) or ""
                if name == "search_personal_knowledge":
                    citations_sent, evs = _maybe_yield_citations(ctx, sent=citations_sent)
                    for ev in evs:
                        yield ev
                elif name == "ask_user_clarify" and ctx.clarify:
                    yield from _yield_clarify_done()
                    return
                continue

            if isinstance(msg, AIMessage) and msg.tool_calls:
                pre_tool_buf.clear()
                for tc in msg.tool_calls:
                    tname = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
                    if tname == "search_personal_knowledge" and not ctx.retrieve_status_sent:
                        ctx.retrieve_status_sent = True
                        yield {
                            "type": "status",
                            "phase": "retrieve",
                            "message": "正在检索个人知识库…",
                        }
                    elif tname == "ask_user_clarify":
                        yield {
                            "type": "status",
                            "phase": "clarify",
                            "message": "正在整理需要向您确认的问题…",
                        }
                usage = _usage_dict_from_message(msg)
                if usage:
                    ctx.usages.append(usage)
                continue

            if _message_has_tool_calls(msg):
                pre_tool_buf.clear()
                continue

            if isinstance(msg, (AIMessage, AIMessageChunk)):
                usage = _usage_dict_from_message(msg)
                if usage:
                    ctx.usages.append(usage)

            text_fragments: list[str] = []
            if isinstance(msg, AIMessageChunk):
                text_fragments = list(_yield_text_from_stream_chunk(msg))
            elif isinstance(msg, AIMessage):
                t = _str_content(msg.content).strip()
                if t:
                    text_fragments = [t]

            if not text_fragments:
                continue

            if saw_tool_message:
                yield from _emit_text_fragments(text_fragments)
                continue

            pre_tool_buf.extend(text_fragments)
            if isinstance(msg, AIMessage) and not msg.tool_calls:
                yield from _emit_text_fragments(pre_tool_buf)
                pre_tool_buf.clear()

    except GraphRecursionError:
        log.warning("LangGraph 工具循环达到 recursion_limit=%s", config.get("recursion_limit"))
        if ctx.clarify:
            yield from _yield_clarify_done()
            return
        yield {
            "type": "error",
            "message": "工具调用轮次超过上限，请简化问题后重试。",
            "message_id": message_id,
        }
        return
    except Exception as e:
        log.exception("LangChain Agent 流式失败：%s", e)
        yield {"type": "error", "message": str(e), "message_id": message_id}
        return

    yield from _flush_pending(ctx)
    if pre_tool_buf and not saw_tool_message:
        yield from _emit_text_fragments(pre_tool_buf)
        pre_tool_buf.clear()
    citations_sent, evs = _maybe_yield_citations(ctx, sent=citations_sent)
    for ev in evs:
        yield ev

    body = "".join(answer_parts).strip() or last_ai_text.strip() or "（模型未返回有效正文。）"
    display, stored = reply_display_stored_fn(
        body,
        session_prefix=session_prefix,
        preface_shown=preface_shown,
        opening_already_in_db=opening_already_in_db,
    )
    disc = settings.disclaimer_text.strip() or None
    chunk_ids = [
        str(x.get("chunk_id"))
        for x in ctx.citations
        if isinstance(x, dict) and x.get("chunk_id") is not None
    ]
    emit_structured_trace(
        settings,
        {
            "event": "agent_chat",
            "message_id": message_id,
            "user_message": user_message.strip()[:2000],
            "chunk_ids": chunk_ids,
            "embed_model": settings.openai_embed_model,
            "chat_model": settings.openai_chat_model,
            "latency_total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
            "usage_tool_rounds": ctx.usages or None,
            "in_fast_session": in_fast_window,
            "path": "langchain_create_agent",
        },
    )
    yield done_event_fn(
        answer=display,
        answer_stored=stored,
        clarify=ctx.clarify,
        disclaimer=disc,
        message_id=message_id,
    )
