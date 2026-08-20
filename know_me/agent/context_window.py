"""
E03 — 多轮上下文：滑动窗口 + 会话摘要（可选 RAG 辅助）+ 字符预算裁剪。

- **滑动窗口**：仅将最近 N 轮 user/assistant 原文送入 Agent。
- **摘要**：较早轮次合并为 `chat_sessions.conversation_summary`，注入 system。
- **RAG**：更新摘要时用「即将滚出窗口」的对话文本检索个人语料，摘要仅归纳对话中已谈内容。
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from know_me.core.settings import IndexSettings
from know_me.rag.llm import chat_complete
from know_me.rag.retrieval import retrieve, retrieved_to_citation_block

log = logging.getLogger(__name__)

_SUMMARY_SYSTEM = """你是 Know Me 会话摘要助手。将「已有摘要」与「新增对话」合并为一份续聊要点（中文书面语）。
须保留（若出现过）：招聘方称呼/身份、招聘岗位、已问已答主题、敏感项是否已谈（薪酬/缺点/地点等）、未决问题。
禁止编造；个人知识库参考片段仅用于核对对话中是否涉及相关事实，不得写入片段中未在对话出现的内容。
输出纯段落，不超过指定字数，不要 Markdown 标题。"""

_SUMMARY_INJECT_HEAD = (
    "\n\n【本会话此前要点 · 自动生成】\n"
    "以下为较早轮次摘要，供续聊衔接；勿与下文本轮用户/助手全文重复展开。\n"
)


class ConversationContextStore(Protocol):
    def history(self, session_id: str) -> list[dict[str, Any]]: ...

    def get_conversation_summary(self, session_id: str) -> tuple[str | None, int]:
        """返回 (摘要正文, 已纳入摘要的最大 seq)；无摘要时 through_seq 为 -1。"""

    def set_conversation_summary(self, session_id: str, summary: str, through_seq: int) -> None: ...


def history_to_chat_messages(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in history:
        role = m.get("role")
        content = m.get("content")
        if role in ("user", "assistant") and isinstance(content, str):
            out.append({"role": role, "content": content})
    return out


def split_leading_opening(hist_sl: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    if hist_sl and hist_sl[0].get("role") == "assistant":
        lone = str(hist_sl[0].get("content") or "").strip()
        if lone:
            return lone, hist_sl[1:]
    return None, hist_sl


def sliding_window_tail(hist_sl: list[dict[str, Any]], window_turns: int) -> list[dict[str, Any]]:
    """保留最近 window_turns 轮（每轮 user+assistant 各一条）。"""
    n = max(1, int(window_turns)) * 2
    if len(hist_sl) <= n:
        return list(hist_sl)
    return list(hist_sl[-n:])


def format_summary_system_suffix(summary: str | None) -> str:
    s = (summary or "").strip()
    if not s:
        return ""
    cap = 2000
    if len(s) > cap:
        s = s[:cap].rstrip() + "…"
    return _SUMMARY_INJECT_HEAD + s


def _transcript_lines(messages: list[dict[str, Any]], *, max_chars: int = 12000) -> str:
    lines: list[str] = []
    used = 0
    for m in messages:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        label = "招聘方" if role == "user" else "助手"
        body = str(m.get("content") or "").strip()
        if not body:
            continue
        line = f"{label}：{body}"
        if used + len(line) + 1 > max_chars:
            lines.append("（…更早内容已省略）")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def _ordered_turn_messages(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = []
    for m in history:
        if m.get("role") in ("user", "assistant"):
            msgs.append(m)
    if msgs and msgs[0].get("role") == "assistant":
        msgs = msgs[1:]
    return msgs


def _messages_before_window(
    history: list[dict[str, Any]],
    window_turns: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """
    返回 (将纳入摘要的较早消息, 滑动窗口内消息, 窗口前最后一条 seq)。
    若无需摘要，前者为空，through 为 -1。
    """
    msgs = _ordered_turn_messages(history)
    win = max(1, int(window_turns)) * 2
    if len(msgs) <= win:
        return [], list(msgs), -1
    older = list(msgs[: len(msgs) - win])
    tail = list(msgs[-win:])
    through = -1
    if older:
        try:
            through = int(older[-1].get("seq", -1))
        except (TypeError, ValueError):
            through = -1
    return older, tail, through


def _rag_hints_for_summary(settings: IndexSettings, transcript: str) -> str:
    if not settings.agent_summary_rag_enabled:
        return ""
    q = transcript.strip()[:800]
    if not q:
        return ""
    try:
        chunks = retrieve(settings, q, top_k=min(3, settings.rag_top_k))
        block = retrieved_to_citation_block(chunks)
        if not block.strip():
            return ""
        cap = max(512, settings.agent_summary_rag_max_chars)
        if len(block) > cap:
            block = block[:cap].rstrip() + "\n（…参考片段已截断）"
        return block
    except Exception as e:
        log.warning("摘要 RAG 检索失败（已跳过）：%s", e)
        return ""


def _summary_incremental_payload(
    hist: list[dict[str, Any]],
    settings: IndexSettings,
    store: ConversationContextStore,
    session_id: str,
) -> tuple[list[dict[str, Any]], str | None, int, str, int] | None:
    """
    若存在待摘要的滑出窗口内容，返回
    (incremental, existing, through_stored, transcript, through_candidate)；否则 None。
    """
    older, _tail, through_candidate = _messages_before_window(
        hist, settings.agent_context_window_turns,
    )
    if not older:
        return None
    existing, through_stored = store.get_conversation_summary(session_id)
    incremental: list[dict[str, Any]] = []
    for m in older:
        try:
            seq = int(m.get("seq", -1))
        except (TypeError, ValueError):
            seq = -1
        if seq > through_stored:
            incremental.append(m)
    if not incremental and not (existing or "").strip():
        incremental = list(older)
    if not incremental and (existing or "").strip():
        return None
    transcript = _transcript_lines(
        [{"role": m.get("role"), "content": m.get("content")} for m in incremental],
    )
    if not transcript.strip():
        return None
    return incremental, existing, through_stored, transcript, through_candidate


def conversation_summary_refresh_needed(
    settings: IndexSettings,
    store: ConversationContextStore,
    session_id: str,
) -> bool:
    """是否值得更新摘要（积压够长、确有滑出窗口内容）。"""
    if not settings.agent_summary_enabled or settings.agent_summary_mode == "off":
        return False
    sid = (session_id or "").strip()
    if not sid:
        return False
    hist = store.history(sid)
    payload = _summary_incremental_payload(hist, settings, store, sid)
    if payload is None:
        return False
    _inc, _ex, _th, transcript, _tc = payload
    min_tx = max(0, settings.agent_summary_min_transcript_chars)
    return len(transcript.strip()) >= min_tx


def refresh_conversation_summary_if_needed(
    settings: IndexSettings,
    store: ConversationContextStore,
    session_id: str,
) -> bool:
    """按需更新摘要；返回是否执行了更新。"""
    if not conversation_summary_refresh_needed(settings, store, session_id):
        return False
    maybe_refresh_conversation_summary(settings, store, session_id)
    return True


def generate_merged_summary(
    settings: IndexSettings,
    *,
    existing_summary: str | None,
    new_transcript: str,
    rag_block: str = "",
) -> str:
    max_out = max(200, settings.agent_summary_max_chars)
    user_parts = []
    if (existing_summary or "").strip():
        user_parts.append(f"【已有摘要】\n{existing_summary.strip()}")
    user_parts.append(f"【新增对话】\n{new_transcript.strip()}")
    if rag_block.strip():
        user_parts.append(f"【个人知识库参考（仅核对用）】\n{rag_block.strip()}")
    user_parts.append(f"请输出合并后的续聊要点，不超过 {max_out} 字。")
    res = chat_complete(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
        model=settings.openai_chat_model,
        messages=[
            {"role": "system", "content": _SUMMARY_SYSTEM},
            {"role": "user", "content": "\n\n".join(user_parts)},
        ],
        temperature=0.1,
        timeout=min(90.0, settings.agent_summary_timeout_sec),
    )
    text = (res.text or "").strip()
    if len(text) > max_out:
        text = text[:max_out].rstrip() + "…"
    return text


def maybe_refresh_conversation_summary(
    settings: IndexSettings,
    store: ConversationContextStore,
    session_id: str,
) -> None:
    """将滑出滑动窗口的轮次并入会话摘要（调用方应先用 `conversation_summary_refresh_needed` 判断）。"""
    if not settings.agent_summary_enabled or settings.agent_summary_mode == "off":
        return
    sid = (session_id or "").strip()
    if not sid:
        return
    hist = store.history(sid)
    payload = _summary_incremental_payload(hist, settings, store, sid)
    if payload is None:
        return
    incremental, existing, through_stored, transcript, through_candidate = payload
    older, _, _ = _messages_before_window(hist, settings.agent_context_window_turns)
    max_out = max(200, settings.agent_summary_max_chars)
    min_tx = max(0, settings.agent_summary_min_transcript_chars)
    if len(transcript.strip()) < min_tx:
        return

    new_through = through_candidate if through_candidate >= 0 else through_stored
    if new_through < 0 and older:
        for m in reversed(hist):
            if m.get("role") in ("user", "assistant"):
                try:
                    new_through = int(m.get("seq", -1))
                    break
                except (TypeError, ValueError):
                    pass

    rag_block = _rag_hints_for_summary(settings, transcript)
    try:
        merged = generate_merged_summary(
            settings,
            existing_summary=existing,
            new_transcript=transcript,
            rag_block=rag_block,
        )
    except Exception as e:
        log.warning("会话摘要生成失败 session=%s：%s", sid[:8], e)
        return

    store.set_conversation_summary(sid, merged, new_through)
    log.info(
        "已更新会话摘要 session=%s len=%s through_seq=%s rag=%s",
        sid[:8],
        len(merged),
        new_through,
        bool(rag_block),
    )


def prepare_history_for_agent(
    history: list[dict[str, Any]],
    *,
    window_turns: int,
) -> list[dict[str, Any]]:
    """滑动窗口：供 Agent 使用的 user/assistant 列表（不含开场 assistant 条）。"""
    _older, tail, _ = _messages_before_window(history, window_turns)
    return [{"role": m.get("role"), "content": m.get("content")} for m in tail]
