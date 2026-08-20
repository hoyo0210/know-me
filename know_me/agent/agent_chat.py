"""
E03 — Agent 编排：LangChain `create_agent`（LangGraph）+ SSE（KM-302 / KM-303）。

流程概要：
0. **新会话 / 纯寒暄**：本地秒回，不跑 Agent。
1. 组装 system（含开场、摘要、招聘方上下文）+ 滑动窗口历史 + 本轮 user，做字符预算裁剪。
2. **LangGraph Agent**（`know_me.agent.langchain_runner`）：`search_personal_knowledge` / `ask_user_clarify` 工具循环，`stream_mode=messages` 映射为 citations / clarify / delta / done。
3. 产出统一事件 dict，供 HTTP 层编码为 SSE。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any, Iterator

from know_me.rag.job_intent import greeting_fast_answer, is_greeting_only_message
from know_me.agent.langchain_runner import iter_langchain_agent_events
from know_me.agent.prompts_agent import AGENT_TOOLS, get_agent_system_prompt
from know_me.agent.context_window import format_summary_system_suffix, prepare_history_for_agent
from know_me.agent.recruiter_job import build_recruiter_context_suffix
from know_me.core.settings import IndexSettings
from know_me.observability.trace_log import emit_structured_trace

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 10
_AGENT_TOOLS_JSON_CHARS = len(json.dumps(AGENT_TOOLS, ensure_ascii=False))
_MAX_USER_MSG_CHARS = 8000


def _approx_chat_payload_chars(messages: list[dict[str, Any]], tools_chars: int) -> int:
    """粗估发往 chat.completions 的 messages + tools JSON 字符量（与网关 token 限制单调相关）。"""
    return len(json.dumps(messages, ensure_ascii=False)) + tools_chars


def _fixed_payload_chars(system_content: str) -> int:
    """system + tools JSON 粗估字符量（不含历史与本轮 user）。"""
    return _approx_chat_payload_chars([{"role": "system", "content": system_content}], _AGENT_TOOLS_JSON_CHARS)


def _resolve_system_body(
    settings: IndexSettings,
    *,
    viewer_suffix: str,
    recruiter_suffix: str,
    summary_suffix: str,
    session_prefix: str | None,
    prefer_slim: bool,
) -> tuple[str, bool]:
    """
    在完整 / 精简 system 间选择，使 system+tools 尽量适配 KNOW_ME_AGENT_CONTEXT_CHAR_BUDGET。
    返回 (system_body, used_slim)。
    """
    budget = settings.agent_context_char_budget
    reserve_user = 1200
    opening = (session_prefix or "").strip()
    opening_note = _SESSION_OPENING_IN_SYSTEM_NOTE if opening else ""

    def pack(slim: bool) -> str:
        base = get_agent_system_prompt(slim=slim) + viewer_suffix + recruiter_suffix + summary_suffix
        if opening:
            op = opening if len(opening) <= 2048 else opening[:2048].rstrip() + "…"
            base += opening_note + op
        return base

    use_slim = prefer_slim
    body = pack(use_slim)
    fixed = _fixed_payload_chars(body)
    if (
        settings.agent_system_auto_slim
        and not prefer_slim
        and fixed > max(budget - reserve_user, int(budget * 0.72))
    ):
        use_slim = True
        body = pack(True)
        fixed = _fixed_payload_chars(body)
        log.info(
            "Agent system 自动切换精简版（fixed≈%s budget=%s；省略 few-shot 与长篇阶段说明）",
            fixed,
            budget,
        )

    if fixed > budget - 400:
        if summary_suffix:
            body = pack(use_slim)
            body = (
                get_agent_system_prompt(slim=use_slim)
                + viewer_suffix
                + recruiter_suffix
                + (opening_note + (opening[:2048] if opening else ""))
            )
            fixed = _fixed_payload_chars(body)
        if fixed > budget - 400 and opening:
            body = (
                get_agent_system_prompt(slim=use_slim)
                + viewer_suffix
                + recruiter_suffix
                + summary_suffix
            )
            fixed = _fixed_payload_chars(body)

    return body, use_slim


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
    fixed = _fixed_payload_chars(system_content)

    def over() -> bool:
        msgs = [{"role": "system", "content": system_content}, *h, {"role": "user", "content": u}]
        return _approx_chat_payload_chars(msgs, _AGENT_TOOLS_JSON_CHARS) > limit

    dropped = 0
    while len(h) >= 2 and over():
        h = h[2:]
        dropped += 2
    if dropped:
        log.warning(
            "为适配 KNOW_ME_AGENT_CONTEXT_CHAR_BUDGET=%s，已丢弃最早 %d 条滑动窗口历史（fixed≈%s）",
            char_budget,
            dropped,
            fixed,
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
        if not h:
            log.error(
                "固定上下文 system+tools≈%s 已超过预算 %s（滑动窗口历史已全部移除后仍不足）。"
                "请调大 KNOW_ME_AGENT_CONTEXT_CHAR_BUDGET、确认 KNOW_ME_AGENT_SYSTEM_AUTO_SLIM=1，或缩短 persona。",
                fixed,
                char_budget,
            )
        else:
            log.error(
                "在 KNOW_ME_AGENT_CONTEXT_CHAR_BUDGET=%s 下仍无法容纳 system+tools+本轮（fixed≈%s）；请调大预算或换更大上下文模型",
                char_budget,
                fixed,
            )
    return h, u


def _reply_display_stored(
    body: str,
    *,
    session_prefix: str | None,
    preface_shown: bool,
    opening_already_in_db: bool,
) -> tuple[str, str]:
    """助手回复的展示文本与入库全文。开场已单独持久化时勿再把开场拼进本条 assistant。"""
    b = (body or "").strip() or "（模型未返回有效正文。）"
    if session_prefix:
        if opening_already_in_db:
            return b, b
        stored = f"{session_prefix}\n\n{b}".strip()
        display = b if preface_shown else stored
        return display, stored
    return b, b


# 部分 OpenAI 兼容服务要求 system 后首条须为 user，不接受「system → assistant(开场) → user」。
_SESSION_OPENING_IN_SYSTEM_NOTE = (
    "\n\n【上下文：以下为已对用户展示的开场白原文，勿逐字重复；请直接针对用户本轮输入作答。】\n"
)


def _viewer_context_suffix(display_name: str | None, role: str | None) -> str:
    """访客称呼与身份，并入 system（单行化、长度封顶）；本段在会话内保持稳定认知。"""
    d = (display_name or "").strip().replace("\r", " ").replace("\n", " ")
    r = (role or "").strip().replace("\r", " ").replace("\n", " ")
    if len(d) > 64:
        d = d[:64].rstrip()
    if len(r) > 64:
        r = r[:64].rstrip()
    if not d and not r:
        return ""
    lines = ["\n\n【当前对话对象 · 招聘方】"]
    if d:
        lines.append(f"称呼：{d}")
    if r:
        lines.append(f"身份定位：{r}")
    lines.append(
        "以上为本会话对对方的稳定认知，全程保持；若用户正文明确要求更改称呼或身份再作调整。"
        "请结合以上背景调整措辞与举例侧重；勿编造对方未提供的隐私或组织细节。"
    )
    return "\n".join(lines)


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
    viewer_display_name: str | None = None,
    viewer_role: str | None = None,
    recruiter_job_title: str | None = None,
    recruiter_contact: str | None = None,
    session_opening_for_context: str | None = None,
    conversation_summary: str | None = None,
) -> Iterator[dict[str, Any]]:
    """
    产出供 SSE 序列化的事件 dict。

    关键字段：
    - `type=session`：分配到的 session_id；可选 `disclaimer`
    - `type=delta`：`text` 增量
    - `type=citations`：`items` 为引用表（检索工具累计）
    - `type=clarify`：`question` 澄清问句
    - `type=status`：`message` 给人看的进度提示（首个 token 前、检索前等，避免界面长时间无反馈）
    - `type=done`：最终 `answer`（当前气泡展示）；若与入库全文不同则另含 `answer_stored`（供服务端 `append_turn`，避免 Web 已单独展示开场时重复）
    - `type=error`：不可恢复错误说明
    """
    mid = message_id or uuid.uuid4().hex
    t0 = time.perf_counter()
    um = user_message.strip()
    raw_sl = _history_messages(history)
    leading_opening: str | None = None
    if raw_sl and raw_sl[0].get("role") == "assistant":
        lone = str(raw_sl[0].get("content") or "").strip()
        if lone:
            leading_opening = lone
    hist_sl = prepare_history_for_agent(
        history,
        window_turns=settings.agent_context_window_turns,
    )
    completed_pairs = len(hist_sl) // 2
    in_fast_window = (
        settings.agent_fast_session_turns > 0
        and completed_pairs < settings.agent_fast_session_turns
    )
    k_base = top_k if top_k is not None else settings.rag_top_k
    k = max(1, min(k_base, settings.agent_fast_top_k)) if in_fast_window else max(1, k_base)
    tool_body_cap = (
        min(settings.agent_tool_result_max_chars, settings.agent_fast_tool_result_max_chars)
        if in_fast_window
        else settings.agent_tool_result_max_chars
    )
    llm_timeout = (
        float(settings.agent_fast_llm_timeout_sec)
        if in_fast_window and settings.agent_fast_llm_timeout_sec is not None
        else 120.0
    )
    max_tool_rounds_eff = (
        min(MAX_TOOL_ROUNDS, settings.agent_fast_max_tool_rounds)
        if in_fast_window
        else MAX_TOOL_ROUNDS
    )
    if in_fast_window:
        log.info(
            "快会话窗口（第 %d/%d 轮内）：top_k=%s、tool 回注上限=%s、工具轮上限=%s、LLM 超时=%ss",
            completed_pairs + 1,
            settings.agent_fast_session_turns,
            k,
            tool_body_cap,
            max_tool_rounds_eff,
            llm_timeout,
        )
    hist_empty = len(hist_sl) == 0
    co = (session_opening_for_context or "").strip()
    lo = (leading_opening or "").strip()
    raw_open = (co or lo).strip()
    session_prefix: str | None = raw_open if raw_open else None
    opening_already_in_db = bool(leading_opening)

    # 新会话：将开场白注入 system（客户端或已从 hist 剥离的持久化开场）
    if hist_empty:
        log.info(
            "新会话：开场上下文 len=%s；preface_shown=%s；leading_opening_in_hist=%s",
            len(raw_open) if raw_open else 0,
            preface_shown,
            bool(leading_opening),
        )
        if is_greeting_only_message(um):
            tail = "好的，请直接说明您希望了解的具体问题。"
            yield {"type": "citations", "items": []}
            yield {"type": "delta", "text": tail if preface_shown else ("\n\n" + tail)}
            display, stored = _reply_display_stored(
                tail,
                session_prefix=session_prefix,
                preface_shown=preface_shown,
                opening_already_in_db=opening_already_in_db,
            )
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
    vctx = _viewer_context_suffix(viewer_display_name, viewer_role)
    rctx = build_recruiter_context_suffix(recruiter_job_title, recruiter_contact)
    summary_suffix = format_summary_system_suffix(conversation_summary)
    system_body, used_slim = _resolve_system_body(
        settings,
        viewer_suffix=vctx,
        recruiter_suffix=rctx,
        summary_suffix=summary_suffix,
        session_prefix=session_prefix,
        prefer_slim=in_fast_window,
    )
    if used_slim and in_fast_window:
        log.debug("快会话窗口内使用精简 system")
    hist_for_llm, um_llm = _trim_hist_and_user_for_budget(
        hist_sl,
        system_content=system_body,
        user_content=um,
        char_budget=settings.agent_context_char_budget,
    )
    um_llm = (um_llm or "").strip() or um.strip() or "请继续。"
    messages = [{"role": "system", "content": system_body}]
    messages.extend(hist_for_llm)
    messages.append({"role": "user", "content": um_llm})
    payload_chars = _approx_chat_payload_chars(messages, _AGENT_TOOLS_JSON_CHARS)

    if payload_chars > settings.agent_context_char_budget:
        fixed = _fixed_payload_chars(system_body)
        yield {
            "type": "error",
            "message": (
                f"当前请求仍超过上下文预算（KNOW_ME_AGENT_CONTEXT_CHAR_BUDGET={settings.agent_context_char_budget}，"
                f"其中 system+tools 约 {fixed} 字符）。"
                "请调大 KNOW_ME_AGENT_CONTEXT_CHAR_BUDGET、保持 KNOW_ME_AGENT_SYSTEM_AUTO_SLIM=1，"
                "或换更大上下文的对话模型。"
            ),
            "message_id": mid,
        }
        return

    yield from iter_langchain_agent_events(
        settings=settings,
        system_body=system_body,
        hist_for_llm=hist_for_llm,
        um_llm=um_llm,
        user_message=um,
        top_k=k,
        tool_body_cap=tool_body_cap,
        in_fast_window=in_fast_window,
        llm_timeout=llm_timeout,
        max_tool_rounds=max_tool_rounds_eff,
        message_id=mid,
        t0=t0,
        session_prefix=session_prefix,
        preface_shown=preface_shown,
        opening_already_in_db=opening_already_in_db,
        done_event_fn=_done_event,
        reply_display_stored_fn=_reply_display_stored,
    )
    return


def run_agent_chat_blocking(
    settings: IndexSettings,
    history: list[dict[str, Any]],
    user_message: str,
    *,
    top_k: int | None = None,
    message_id: str | None = None,
    on_status: Callable[[dict[str, Any]], None] | None = None,
    preface_shown: bool = False,
    viewer_display_name: str | None = None,
    viewer_role: str | None = None,
    recruiter_job_title: str | None = None,
    recruiter_contact: str | None = None,
    session_opening_for_context: str | None = None,
    conversation_summary: str | None = None,
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
        viewer_display_name=viewer_display_name,
        viewer_role=viewer_role,
        recruiter_job_title=recruiter_job_title,
        recruiter_contact=recruiter_contact,
        session_opening_for_context=session_opening_for_context,
        conversation_summary=conversation_summary,
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
