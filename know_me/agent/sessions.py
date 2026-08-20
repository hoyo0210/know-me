"""
E03 — 多轮会话存储（KM-301）。

保留 `user` / `assistant` 文本轮次；**首条可为 assistant**（POST /session 写入的模板开场白），随后仍为用户与助手交替，便于与 OpenAI 风格消息对齐；工具调用细节不持久化。
可选在会话维度持久化访客称呼与身份、招聘岗位与联系方式（SQLite 为 `chat_sessions` 列，内存版为进程内 dict），供 `/chat` 每轮注入 Agent。
默认通过 `make_chat_session_store(settings)` 使用 **SQLite**（`KNOW_ME_CHAT_SQLITE_*`）；可关闭回退为进程内字典。
assistant 行可带 `message_id`（与 SSE `done.message_id` 一致）与 `vote`（-1/0/1），供点赞点踩写入聊天记录；每条消息带 `created_at_ms`。
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from know_me.agent.message_versions import (
    BRANCH_EDIT,
    BRANCH_INITIAL,
    BRANCH_REGENERATE,
    BRANCH_TRUNCATED,
    branch_label,
    enrich_version_fields,
)
from know_me.core.settings import IndexSettings


def _now_ms() -> int:
    return int(time.time() * 1000)


class ChatSessionStore:
    """线程安全的 session_id → 消息列表。"""

    def __init__(self, max_turns: int) -> None:
        self._max_turns = max(1, max_turns)
        self._lock = threading.Lock()
        self._sessions: dict[str, list[dict[str, Any]]] = {}
        self._viewer: dict[str, tuple[str, str]] = {}
        self._recruiter: dict[str, tuple[str, str | None]] = {}
        self._summary: dict[str, tuple[str, int]] = {}
        self._versions: dict[tuple[str, int], list[dict[str, Any]]] = {}

    def ensure_session(self, session_id: str | None) -> str:
        """若未传 session_id 则新建 UUID；已存在则复用。"""
        sid = (session_id or "").strip() or str(uuid.uuid4())
        with self._lock:
            self._sessions.setdefault(sid, [])
        return sid

    def _vkey(self, session_id: str, seq: int) -> tuple[str, int]:
        return ((session_id or "").strip(), int(seq))

    def _list_versions(self, session_id: str, seq: int) -> list[dict[str, Any]]:
        return list(self._versions.get(self._vkey(session_id, seq), []))

    def _next_vi(self, session_id: str, seq: int) -> int:
        vers = self._list_versions(session_id, seq)
        return (max((int(v["version_index"]) for v in vers), default=-1) + 1) if vers else 0

    def _push_version(
        self,
        session_id: str,
        seq: int,
        *,
        role: str,
        content: str,
        message_id: str | None = None,
        vote: int | None = None,
        branch_kind: str,
    ) -> int:
        key = self._vkey(session_id, seq)
        vers = self._versions.setdefault(key, [])
        vi = self._next_vi(session_id, seq)
        vers.append({
            "version_index": vi,
            "role": role,
            "content": content,
            "message_id": message_id,
            "vote": vote,
            "branch_kind": branch_kind,
            "branch_label": branch_label(branch_kind),
            "created_at_ms": _now_ms(),
        })
        return vi

    def _snapshot_msg(self, session_id: str, seq: int, branch_kind: str) -> None:
        with self._lock:
            hist = self._sessions.get(session_id, [])
            if seq < 0 or seq >= len(hist):
                return
            m = hist[seq]
            vers = self._list_versions(session_id, seq)
            if (
                vers
                and vers[-1].get("content") == m.get("content")
                and vers[-1].get("branch_kind") == branch_kind
            ):
                return
            self._push_version(
                session_id, seq,
                role=str(m.get("role") or ""),
                content=str(m.get("content") or ""),
                message_id=m.get("message_id"),
                vote=m.get("vote"),
                branch_kind=branch_kind,
            )

    def _active_version_index(self, session_id: str, seq: int) -> int:
        with self._lock:
            hist = self._sessions.get(session_id, [])
            if seq < 0 or seq >= len(hist):
                return 0
            m = hist[seq]
            vers = self._list_versions(session_id, seq)
            if not vers:
                return 0
            stored = m.get("active_version_index")
            if stored is not None:
                try:
                    vi = int(stored)
                    if any(int(v["version_index"]) == vi for v in vers):
                        return vi
                except (TypeError, ValueError):
                    pass
            for v in reversed(vers):
                if (
                    v.get("role") == m.get("role")
                    and str(v.get("content") or "") == str(m.get("content") or "")
                    and str(v.get("message_id") or "") == str(m.get("message_id") or "")
                    and v.get("vote") == m.get("vote")
                ):
                    return int(v["version_index"])
            return int(vers[-1]["version_index"])

    def history(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            raw = list(self._sessions.get(session_id, []))
        out: list[dict[str, Any]] = []
        for i, m in enumerate(raw):
            d = dict(m)
            d["seq"] = i
            vers = self._list_versions(session_id, i)
            fields = enrich_version_fields(vers, self._active_version_index(session_id, i))
            if fields:
                d.update(fields)
            out.append(d)
        return out

    def activate_message_version(
        self,
        session_id: str,
        anchor_seq: int,
        version_index: int,
    ) -> dict[str, Any] | None:
        sid = (session_id or "").strip()
        if not sid or anchor_seq < 0:
            return None
        with self._lock:
            hist = self._sessions.get(sid)
            if not hist or anchor_seq >= len(hist):
                return None
            vers = self._list_versions(sid, anchor_seq)
            target = next((v for v in vers if int(v["version_index"]) == int(version_index)), None)
            if not target:
                return None
            hist[anchor_seq]["role"] = target["role"]
            hist[anchor_seq]["content"] = target["content"]
            if target.get("message_id"):
                hist[anchor_seq]["message_id"] = target["message_id"]
            else:
                hist[anchor_seq].pop("message_id", None)
            if "vote" in target:
                hist[anchor_seq]["vote"] = target["vote"]
            hist[anchor_seq]["created_at_ms"] = target.get("created_at_ms", _now_ms())
            hist[anchor_seq]["active_version_index"] = int(version_index)
        return dict(target)

    def get_session_viewer(self, session_id: str) -> tuple[str | None, str | None]:
        sid = (session_id or "").strip()
        if not sid:
            return (None, None)
        with self._lock:
            t = self._viewer.get(sid)
        if not t:
            return (None, None)
        return (t[0], t[1])

    def set_session_viewer(self, session_id: str, display_name: str, role: str) -> None:
        dn = (display_name or "").strip().replace("\r", " ").replace("\n", " ")[:64]
        rl = (role or "").strip().replace("\r", " ").replace("\n", " ")[:64]
        if not dn or not rl:
            return
        sid = (session_id or "").strip()
        if not sid:
            return
        with self._lock:
            self._sessions.setdefault(sid, [])
            self._viewer[sid] = (dn, rl)

    def get_conversation_summary(self, session_id: str) -> tuple[str | None, int]:
        sid = (session_id or "").strip()
        if not sid:
            return (None, -1)
        with self._lock:
            t = self._summary.get(sid)
        if not t:
            return (None, -1)
        return (t[0], t[1])

    def set_conversation_summary(self, session_id: str, summary: str, through_seq: int) -> None:
        sid = (session_id or "").strip()
        body = (summary or "").strip()
        if not sid or not body:
            return
        with self._lock:
            self._summary[sid] = (body[:8000], int(through_seq))

    def get_session_recruiter_context(self, session_id: str) -> tuple[str | None, str | None]:
        sid = (session_id or "").strip()
        if not sid:
            return (None, None)
        with self._lock:
            t = self._recruiter.get(sid)
        if not t:
            return (None, None)
        return (t[0], t[1])

    def set_session_recruiter_context(
        self,
        session_id: str,
        job_title: str,
        contact: str | None = None,
    ) -> None:
        job = (job_title or "").strip().replace("\r", " ").replace("\n", " ")[:256]
        if not job:
            return
        sid = (session_id or "").strip()
        if not sid:
            return
        ct: str | None = None
        if contact is not None:
            c = str(contact).strip().replace("\r", " ").replace("\n", " ")[:128]
            ct = c if c else None
        with self._lock:
            self._sessions.setdefault(sid, [])
            self._recruiter[sid] = (job, ct)

    def apply_regenerate_at_assistant(self, session_id: str, assistant_seq: int) -> bool:
        sid = (session_id or "").strip()
        if not sid or assistant_seq < 0:
            return False
        with self._lock:
            hist = self._sessions.get(sid)
            if not hist or assistant_seq >= len(hist):
                return False
            if hist[assistant_seq].get("role") != "assistant":
                return False
            for i in range(assistant_seq, len(hist)):
                self._snapshot_msg(sid, i, BRANCH_REGENERATE if i == assistant_seq else BRANCH_TRUNCATED)
            del hist[assistant_seq:]
        return True

    def apply_edit_user_message(self, session_id: str, user_seq: int, new_content: str) -> bool:
        sid = (session_id or "").strip()
        nc = (new_content or "").strip()
        if not sid or not nc or user_seq < 0:
            return False
        with self._lock:
            hist = self._sessions.get(sid)
            if not hist or user_seq >= len(hist):
                return False
            if hist[user_seq].get("role") != "user":
                return False
            self._snapshot_msg(sid, user_seq, BRANCH_EDIT)
            for i in range(user_seq + 1, len(hist)):
                self._snapshot_msg(sid, i, BRANCH_TRUNCATED)
            hist[user_seq]["content"] = nc
            hist[user_seq]["created_at_ms"] = _now_ms()
            del hist[user_seq + 1 :]
            vi = self._push_version(
                sid, user_seq, role="user", content=nc, branch_kind=BRANCH_EDIT,
            )
            hist[user_seq]["active_version_index"] = vi
        return True

    def append_turn(
        self,
        session_id: str,
        user_content: str,
        assistant_content: str,
        *,
        assistant_message_id: str | None = None,
    ) -> int | None:
        """追加一轮 user + assistant，并按配置裁剪；返回 assistant 在列表中的下标。"""
        with self._lock:
            hist = self._sessions.setdefault(session_id, [])
            useq = len(hist)
            hist.append({"role": "user", "content": user_content, "created_at_ms": _now_ms()})
            vi_u = self._push_version(
                session_id, useq, role="user", content=user_content, branch_kind=BRANCH_INITIAL,
            )
            hist[useq]["active_version_index"] = vi_u
            amsg: dict[str, Any] = {
                "role": "assistant",
                "content": assistant_content,
                "created_at_ms": _now_ms(),
            }
            if assistant_message_id and str(assistant_message_id).strip():
                amsg["message_id"] = str(assistant_message_id).strip()
            aseq = len(hist)
            hist.append(amsg)
            vi_a = self._push_version(
                session_id, aseq,
                role="assistant",
                content=assistant_content,
                message_id=amsg.get("message_id"),
                branch_kind=BRANCH_INITIAL,
            )
            hist[aseq]["active_version_index"] = vi_a
            self._trim(session_id, hist)
            return len(hist) - 1

    def append_assistant_reply(
        self,
        session_id: str,
        assistant_content: str,
        *,
        assistant_message_id: str | None = None,
        version_branch_kind: str = BRANCH_REGENERATE,
    ) -> int | None:
        with self._lock:
            hist = self._sessions.setdefault(session_id, [])
            if not hist or hist[-1].get("role") != "user":
                return None
            amsg: dict[str, Any] = {"role": "assistant", "content": assistant_content, "created_at_ms": _now_ms()}
            if assistant_message_id and str(assistant_message_id).strip():
                amsg["message_id"] = str(assistant_message_id).strip()
            aseq = len(hist)
            hist.append(amsg)
            vi = self._push_version(
                session_id, aseq,
                role="assistant",
                content=assistant_content,
                message_id=amsg.get("message_id"),
                branch_kind=version_branch_kind,
            )
            hist[aseq]["active_version_index"] = vi
            self._trim(session_id, hist)
            return len(hist) - 1

    def append_session_opening(self, session_id: str, opening: str) -> int | None:
        """尚无消息时写入一条 assistant 开场白（与 POST /session 模板一致），便于历史与刷新展示；已写入相同内容则幂等返回 0。"""
        text = (opening or "").strip()
        sid = (session_id or "").strip()
        if not text or not sid:
            return None
        with self._lock:
            hist = self._sessions.setdefault(sid, [])
            if len(hist) == 0:
                hist.append({"role": "assistant", "content": text, "created_at_ms": _now_ms()})
                vi = self._push_version(sid, 0, role="assistant", content=text, branch_kind=BRANCH_INITIAL)
                hist[0]["active_version_index"] = vi
                self._trim(sid, hist)
                return len(hist) - 1
            if (
                len(hist) == 1
                and hist[0].get("role") == "assistant"
                and str(hist[0].get("content", "")).strip() == text
            ):
                return 0
            return None

    def set_message_vote(self, session_id: str, message_id: str, vote: int) -> bool:
        """将指定 assistant 消息的 vote 写入内存列表；vote ∈ {-1,0,1}。"""
        if vote not in (-1, 0, 1):
            return False
        mid = (message_id or "").strip()
        sid = (session_id or "").strip()
        if not mid or not sid:
            return False
        with self._lock:
            hist = self._sessions.get(sid)
            if not hist:
                return False
            updated = False
            for m in hist:
                if m.get("role") == "assistant" and str(m.get("message_id") or "") == mid:
                    m["vote"] = vote
                    updated = True
            for key, vers in self._versions.items():
                if key[0] != sid:
                    continue
                for v in vers:
                    if str(v.get("message_id") or "") == mid:
                        v["vote"] = vote
                        updated = True
            return updated

    def _trim(self, session_id: str, hist: list[dict[str, Any]]) -> None:
        max_msgs = self._max_turns * 2
        if len(hist) <= max_msgs:
            return
        excess = len(hist) - max_msgs
        del hist[0:excess]
        sid = (session_id or "").strip()
        if not sid:
            return
        for seq in range(excess):
            self._versions.pop(self._vkey(sid, seq), None)
        rekey: list[tuple[tuple[str, int], list[dict[str, Any]]]] = []
        for key, vers in list(self._versions.items()):
            if key[0] != sid or key[1] < excess:
                continue
            rekey.append((key, vers))
        for key, vers in sorted(rekey, key=lambda x: x[0][1]):
            self._versions.pop(key, None)
            self._versions[self._vkey(sid, key[1] - excess)] = vers


def make_chat_session_store(settings: IndexSettings):
    """按配置返回 SQLite 持久化存储或内存存储。"""
    if settings.chat_sqlite_enabled:
        from know_me.agent.chat_db import SqliteChatSessionStore

        return SqliteChatSessionStore(settings.chat_sqlite_path, settings.chat_history_max_turns)
    return ChatSessionStore(settings.chat_history_max_turns)
