"""
E03 — 多轮会话的 SQLite 持久化（与 `ChatSessionStore` 相同方法签名）。

表结构：`chat_sessions` 记录会话元数据（含 `viewer_display_name` / `viewer_role`、`recruiter_job_title`、`recruiter_contact` 供 Agent 常驻上下文）；`chat_messages` 按 `seq` 有序存放 user/assistant 文本。**首条可为 assistant**（与 `/session` 模板开场白一致，便于历史回放）。
与进程内内存版一致，仅持久化 `user`/`assistant` 文本轮次；每条消息带 `created_at_ms`（Unix 毫秒）；assistant 可带 `message_id`、`vote`（点赞点踩）。
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from know_me.agent.message_versions import (
    BRANCH_EDIT,
    BRANCH_INITIAL,
    BRANCH_REGENERATE,
    BRANCH_TRUNCATED,
    activate_sqlite_version,
    active_version_index_sqlite,
    enrich_version_fields,
    init_versions_schema,
    insert_sqlite_version,
    list_sqlite_versions,
    set_message_active_version_sqlite,
    set_vote_sqlite_by_message_id,
    snapshot_sqlite_from_seq,
    snapshot_sqlite_message,
)


class SqliteChatSessionStore:
    """线程安全；单连接 + 互斥，适配 FastAPI 线程池内同步读写。"""

    def __init__(self, db_path: Path, max_turns: int) -> None:
        self._path = Path(db_path).expanduser()
        self._max_turns = max(1, max_turns)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._path.resolve()), check_same_thread=False, timeout=60.0)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._init_schema()
        return self._conn

    def _migrate_columns(self, con: sqlite3.Connection) -> None:
        cols = {str(r[1]) for r in con.execute("PRAGMA table_info(chat_messages)")}
        if "message_id" not in cols:
            con.execute("ALTER TABLE chat_messages ADD COLUMN message_id TEXT")
        if "vote" not in cols:
            con.execute("ALTER TABLE chat_messages ADD COLUMN vote INTEGER")
        if "created_at_ms" not in cols:
            con.execute("ALTER TABLE chat_messages ADD COLUMN created_at_ms INTEGER")
            con.execute(
                """
                UPDATE chat_messages
                SET created_at_ms = (
                    SELECT s.updated_at_ms FROM chat_sessions s
                    WHERE s.session_id = chat_messages.session_id
                )
                WHERE created_at_ms IS NULL
                """,
            )
        if "active_version_index" not in cols:
            con.execute("ALTER TABLE chat_messages ADD COLUMN active_version_index INTEGER")
        con.commit()

    def _migrate_session_viewer_columns(self, con: sqlite3.Connection) -> None:
        cols = {str(r[1]) for r in con.execute("PRAGMA table_info(chat_sessions)")}
        if "viewer_display_name" not in cols:
            con.execute("ALTER TABLE chat_sessions ADD COLUMN viewer_display_name TEXT")
        if "viewer_role" not in cols:
            con.execute("ALTER TABLE chat_sessions ADD COLUMN viewer_role TEXT")
        if "recruiter_job_title" not in cols:
            con.execute("ALTER TABLE chat_sessions ADD COLUMN recruiter_job_title TEXT")
        if "recruiter_contact" not in cols:
            con.execute("ALTER TABLE chat_sessions ADD COLUMN recruiter_contact TEXT")
        if "conversation_summary" not in cols:
            con.execute("ALTER TABLE chat_sessions ADD COLUMN conversation_summary TEXT")
        if "summary_through_seq" not in cols:
            con.execute("ALTER TABLE chat_sessions ADD COLUMN summary_through_seq INTEGER")
        con.commit()

    def _init_schema(self) -> None:
        assert self._conn is not None
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chat_sessions (
                session_id TEXT PRIMARY KEY,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                UNIQUE(session_id, seq)
            );
            CREATE INDEX IF NOT EXISTS idx_chat_messages_session_seq
                ON chat_messages(session_id, seq);
            """,
        )
        self._conn.commit()
        self._migrate_columns(self._conn)
        self._migrate_session_viewer_columns(self._conn)
        init_versions_schema(self._conn)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_messages_mid ON chat_messages(session_id, message_id)"
            " WHERE message_id IS NOT NULL",
        )
        self._conn.commit()

    def ensure_session(self, session_id: str | None) -> str:
        sid = (session_id or "").strip() or str(uuid.uuid4())
        now = int(time.time() * 1000)
        with self._lock:
            con = self._connect()
            con.execute(
                "INSERT OR IGNORE INTO chat_sessions(session_id, created_at_ms, updated_at_ms) VALUES (?,?,?)",
                (sid, now, now),
            )
            con.commit()
        return sid

    def _row_to_msg(self, r: sqlite3.Row) -> dict[str, Any]:
        rmap = dict(r)
        d: dict[str, Any] = {
            "seq": int(rmap["seq"]) if rmap.get("seq") is not None else 0,
            "role": str(rmap.get("role", "")),
            "content": str(rmap.get("content", "")),
        }
        mid = rmap.get("message_id")
        if mid is not None and str(mid).strip():
            d["message_id"] = str(mid).strip()
        v = rmap.get("vote")
        if v is not None:
            try:
                iv = int(v)
                if iv in (-1, 0, 1):
                    d["vote"] = iv
            except (TypeError, ValueError):
                pass
        ts = rmap.get("created_at_ms")
        if ts is not None:
            try:
                d["created_at_ms"] = int(ts)
            except (TypeError, ValueError):
                pass
        return d

    def _enrich_history_row(self, con: sqlite3.Connection, sid: str, msg: dict[str, Any]) -> dict[str, Any]:
        seq = int(msg.get("seq", -1))
        if seq < 0:
            return msg
        versions = list_sqlite_versions(con, sid, seq)
        fields = enrich_version_fields(versions, active_version_index_sqlite(con, sid, seq))
        if not fields:
            return msg
        out = dict(msg)
        out.update(fields)
        return out

    def history(self, session_id: str) -> list[dict[str, Any]]:
        sid = (session_id or "").strip()
        with self._lock:
            con = self._connect()
            rows = con.execute(
                "SELECT seq, role, content, message_id, vote, created_at_ms FROM chat_messages"
                " WHERE session_id=? ORDER BY seq ASC",
                (sid,),
            ).fetchall()
            return [self._enrich_history_row(con, sid, self._row_to_msg(r)) for r in rows]

    def activate_message_version(
        self,
        session_id: str,
        anchor_seq: int,
        version_index: int,
    ) -> dict[str, Any] | None:
        """仅切换当前展示/入库活跃副本，不触发对话生成。"""
        sid = (session_id or "").strip()
        if not sid or anchor_seq < 0 or version_index < 0:
            return None
        with self._lock:
            con = self._connect()
            v = activate_sqlite_version(con, sid, anchor_seq, version_index)
            if v is None:
                return None
            con.execute(
                "UPDATE chat_sessions SET updated_at_ms=? WHERE session_id=?",
                (int(time.time() * 1000), sid),
            )
            con.commit()
        return v

    def get_session_viewer(self, session_id: str) -> tuple[str | None, str | None]:
        sid = (session_id or "").strip()
        if not sid:
            return (None, None)
        with self._lock:
            con = self._connect()
            row = con.execute(
                "SELECT viewer_display_name, viewer_role FROM chat_sessions WHERE session_id=?",
                (sid,),
            ).fetchone()
        if not row:
            return (None, None)
        dn = row["viewer_display_name"]
        rl = row["viewer_role"]
        return (
            str(dn).strip() if dn is not None and str(dn).strip() else None,
            str(rl).strip() if rl is not None and str(rl).strip() else None,
        )

    def set_session_viewer(self, session_id: str, display_name: str, role: str) -> None:
        """称呼与身份均非空时写入 chat_sessions，供后续轮次缺省注入 Agent。"""
        dn = (display_name or "").strip().replace("\r", " ").replace("\n", " ")[:64]
        rl = (role or "").strip().replace("\r", " ").replace("\n", " ")[:64]
        if not dn or not rl:
            return
        sid = (session_id or "").strip()
        if not sid:
            return
        now = int(time.time() * 1000)
        with self._lock:
            con = self._connect()
            con.execute(
                "UPDATE chat_sessions SET viewer_display_name=?, viewer_role=?, updated_at_ms=? WHERE session_id=?",
                (dn, rl, now, sid),
            )
            con.commit()

    def get_conversation_summary(self, session_id: str) -> tuple[str | None, int]:
        sid = (session_id or "").strip()
        if not sid:
            return (None, -1)
        with self._lock:
            con = self._connect()
            row = con.execute(
                "SELECT conversation_summary, summary_through_seq FROM chat_sessions WHERE session_id=?",
                (sid,),
            ).fetchone()
        if not row:
            return (None, -1)
        raw = row["conversation_summary"]
        text = str(raw).strip() if raw is not None and str(raw).strip() else None
        try:
            through = int(row["summary_through_seq"]) if row["summary_through_seq"] is not None else -1
        except (TypeError, ValueError):
            through = -1
        return (text, through)

    def set_conversation_summary(self, session_id: str, summary: str, through_seq: int) -> None:
        sid = (session_id or "").strip()
        body = (summary or "").strip()
        if not sid or not body:
            return
        try:
            through = int(through_seq)
        except (TypeError, ValueError):
            through = -1
        now = int(time.time() * 1000)
        with self._lock:
            con = self._connect()
            con.execute(
                "UPDATE chat_sessions SET conversation_summary=?, summary_through_seq=?, updated_at_ms=? WHERE session_id=?",
                (body[:8000], through, now, sid),
            )
            con.commit()

    def get_session_recruiter_context(self, session_id: str) -> tuple[str | None, str | None]:
        sid = (session_id or "").strip()
        if not sid:
            return (None, None)
        with self._lock:
            con = self._connect()
            row = con.execute(
                "SELECT recruiter_job_title, recruiter_contact FROM chat_sessions WHERE session_id=?",
                (sid,),
            ).fetchone()
        if not row:
            return (None, None)
        job = row["recruiter_job_title"]
        contact = row["recruiter_contact"]
        return (
            str(job).strip() if job is not None and str(job).strip() else None,
            str(contact).strip() if contact is not None and str(contact).strip() else None,
        )

    def set_session_recruiter_context(
        self,
        session_id: str,
        job_title: str,
        contact: str | None = None,
    ) -> None:
        """招聘岗位必填写入；联系方式可选。"""
        job = (job_title or "").strip().replace("\r", " ").replace("\n", " ")[:256]
        if not job:
            return
        ct: str | None = None
        if contact is not None:
            c = str(contact).strip().replace("\r", " ").replace("\n", " ")[:128]
            ct = c if c else None
        sid = (session_id or "").strip()
        if not sid:
            return
        now = int(time.time() * 1000)
        with self._lock:
            con = self._connect()
            con.execute(
                "UPDATE chat_sessions SET recruiter_job_title=?, recruiter_contact=?, updated_at_ms=? WHERE session_id=?",
                (job, ct, now, sid),
            )
            con.commit()

    def _trim_session(self, con: sqlite3.Connection, session_id: str) -> None:
        max_msgs = self._max_turns * 2
        cnt_row = con.execute(
            "SELECT COUNT(*) AS c FROM chat_messages WHERE session_id=?",
            (session_id,),
        ).fetchone()
        cnt = int(cnt_row["c"]) if cnt_row else 0
        if cnt > max_msgs:
            excess = cnt - max_msgs
            to_del = con.execute(
                "SELECT id FROM chat_messages WHERE session_id=? ORDER BY seq ASC LIMIT ?",
                (session_id, excess),
            ).fetchall()
            for r in to_del:
                seq_row = con.execute(
                    "SELECT seq FROM chat_messages WHERE id=?",
                    (int(r["id"]),),
                ).fetchone()
                if seq_row is not None:
                    con.execute(
                        "DELETE FROM chat_message_versions WHERE session_id=? AND anchor_seq=?",
                        (session_id, int(seq_row["seq"])),
                    )
                con.execute("DELETE FROM chat_messages WHERE id=?", (int(r["id"]),))

    def apply_regenerate_at_assistant(self, session_id: str, assistant_seq: int) -> bool:
        """归档该 assistant 及之后消息，再删除活跃行以便重答。"""
        sid = (session_id or "").strip()
        if not sid or assistant_seq < 0:
            return False
        with self._lock:
            con = self._connect()
            row = con.execute(
                "SELECT role FROM chat_messages WHERE session_id=? AND seq=?",
                (sid, assistant_seq),
            ).fetchone()
            if not row or str(row["role"]) != "assistant":
                return False
            snapshot_sqlite_message(con, sid, assistant_seq, branch_kind=BRANCH_REGENERATE)
            snapshot_sqlite_from_seq(
                con, sid, assistant_seq + 1, branch_kind=BRANCH_TRUNCATED,
            )
            con.execute("DELETE FROM chat_messages WHERE session_id=? AND seq>=?", (sid, assistant_seq))
            con.commit()
        return True

    def apply_edit_user_message(self, session_id: str, user_seq: int, new_content: str) -> bool:
        """归档旧 user 及之后消息，更新 user 正文并删除其后活跃消息。"""
        sid = (session_id or "").strip()
        nc = (new_content or "").strip()
        if not sid or not nc or user_seq < 0:
            return False
        with self._lock:
            con = self._connect()
            row = con.execute(
                "SELECT role FROM chat_messages WHERE session_id=? AND seq=?",
                (sid, user_seq),
            ).fetchone()
            if not row or str(row["role"]) != "user":
                return False
            snapshot_sqlite_message(con, sid, user_seq, branch_kind=BRANCH_EDIT)
            snapshot_sqlite_from_seq(con, sid, user_seq + 1, branch_kind=BRANCH_TRUNCATED)
            con.execute("DELETE FROM chat_messages WHERE session_id=? AND seq>?", (sid, user_seq))
            now = int(time.time() * 1000)
            con.execute(
                "UPDATE chat_messages SET content=?, created_at_ms=? WHERE session_id=? AND seq=? AND role='user'",
                (nc, now, sid, user_seq),
            )
            vi = insert_sqlite_version(
                con, sid, user_seq,
                role="user", content=nc, message_id=None, vote=None,
                branch_kind=BRANCH_EDIT, created_at_ms=now,
            )
            set_message_active_version_sqlite(con, sid, user_seq, vi)
            con.commit()
        return True

    def append_turn(
        self,
        session_id: str,
        user_content: str,
        assistant_content: str,
        *,
        assistant_message_id: str | None = None,
    ) -> int | None:
        """追加一轮 user+assistant；返回 assistant 行的 seq，未写入则返回 None。"""
        aid = (assistant_message_id or "").strip() or None
        with self._lock:
            con = self._connect()
            now = int(time.time() * 1000)
            row = con.execute(
                "SELECT COALESCE(MAX(seq), -1) AS m FROM chat_messages WHERE session_id=?",
                (session_id,),
            ).fetchone()
            start = int(row["m"]) + 1
            user_ms = now
            asst_ms = int(time.time() * 1000)
            con.execute(
                "INSERT INTO chat_messages(session_id, seq, role, content, message_id, vote, created_at_ms)"
                " VALUES (?,?,?,?,?,NULL,?)",
                (session_id, start, "user", user_content, None, user_ms),
            )
            vi_u = insert_sqlite_version(
                con, session_id, start,
                role="user", content=user_content, message_id=None, vote=None,
                branch_kind=BRANCH_INITIAL, created_at_ms=user_ms,
            )
            set_message_active_version_sqlite(con, session_id, start, vi_u)
            con.execute(
                "INSERT INTO chat_messages(session_id, seq, role, content, message_id, vote, created_at_ms)"
                " VALUES (?,?,?,?,?,NULL,?)",
                (session_id, start + 1, "assistant", assistant_content, aid, asst_ms),
            )
            vi_a = insert_sqlite_version(
                con, session_id, start + 1,
                role="assistant", content=assistant_content, message_id=aid, vote=None,
                branch_kind=BRANCH_INITIAL, created_at_ms=asst_ms,
            )
            set_message_active_version_sqlite(con, session_id, start + 1, vi_a)
            con.execute(
                "UPDATE chat_sessions SET updated_at_ms=? WHERE session_id=?",
                (now, session_id),
            )
            self._trim_session(con, session_id)
            con.commit()
            return start + 1

    def append_assistant_reply(
        self,
        session_id: str,
        assistant_content: str,
        *,
        assistant_message_id: str | None = None,
        version_branch_kind: str = BRANCH_REGENERATE,
    ) -> int | None:
        """在末尾已有 user、尚无对应 assistant 时追加一条 assistant；否则返回 None。"""
        aid = (assistant_message_id or "").strip() or None
        with self._lock:
            con = self._connect()
            now = int(time.time() * 1000)
            last = con.execute(
                "SELECT seq, role FROM chat_messages WHERE session_id=? ORDER BY seq DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            if not last or str(last["role"]) != "user":
                return None
            ns = int(last["seq"]) + 1
            con.execute(
                "INSERT INTO chat_messages(session_id, seq, role, content, message_id, vote, created_at_ms)"
                " VALUES (?,?,?,?,?,NULL,?)",
                (session_id, ns, "assistant", assistant_content, aid, now),
            )
            vi = insert_sqlite_version(
                con, session_id, ns,
                role="assistant", content=assistant_content, message_id=aid, vote=None,
                branch_kind=version_branch_kind, created_at_ms=now,
            )
            set_message_active_version_sqlite(con, session_id, ns, vi)
            con.execute(
                "UPDATE chat_sessions SET updated_at_ms=? WHERE session_id=?",
                (now, session_id),
            )
            self._trim_session(con, session_id)
            con.commit()
            return ns

    def append_session_opening(self, session_id: str, opening: str) -> int | None:
        """尚无消息时插入 seq=0 的 assistant 开场白；已存在相同唯一开场则幂等返回 0。"""
        text = (opening or "").strip()
        sid = (session_id or "").strip()
        if not text or not sid:
            return None
        with self._lock:
            con = self._connect()
            now = int(time.time() * 1000)
            cnt_row = con.execute(
                "SELECT COUNT(*) AS c FROM chat_messages WHERE session_id=?",
                (sid,),
            ).fetchone()
            cnt = int(cnt_row["c"]) if cnt_row else 0
            if cnt == 0:
                con.execute(
                    "INSERT INTO chat_messages(session_id, seq, role, content, message_id, vote, created_at_ms)"
                    " VALUES (?,?,?,?,?,NULL,?)",
                    (sid, 0, "assistant", text, None, now),
                )
                vi = insert_sqlite_version(
                    con, sid, 0,
                    role="assistant", content=text, message_id=None, vote=None,
                    branch_kind=BRANCH_INITIAL, created_at_ms=now,
                )
                set_message_active_version_sqlite(con, sid, 0, vi)
                con.execute(
                    "UPDATE chat_sessions SET updated_at_ms=? WHERE session_id=?",
                    (now, sid),
                )
                self._trim_session(con, sid)
                con.commit()
                return 0
            if cnt == 1:
                row = con.execute(
                    "SELECT role, content FROM chat_messages WHERE session_id=? ORDER BY seq ASC LIMIT 1",
                    (sid,),
                ).fetchone()
                if row and str(row["role"]) == "assistant" and str(row["content"] or "").strip() == text:
                    return 0
            return None

    def set_message_vote(self, session_id: str, message_id: str, vote: int) -> bool:
        if vote not in (-1, 0, 1):
            return False
        mid = (message_id or "").strip()
        sid = (session_id or "").strip()
        if not mid or not sid:
            return False
        with self._lock:
            con = self._connect()
            ok = set_vote_sqlite_by_message_id(con, sid, mid, vote)
            con.commit()
            return ok
