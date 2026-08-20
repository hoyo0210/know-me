"""会话消息多版本：重新生成 / 编辑前的快照与仅查看切换（不写 LLM）。"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

BRANCH_INITIAL = "initial"
BRANCH_REGENERATE = "regenerate"
BRANCH_EDIT = "edit"
BRANCH_TRUNCATED = "truncated"

DISPLAY_VERSION_BRANCHES = frozenset({
    BRANCH_INITIAL,
    BRANCH_REGENERATE,
    BRANCH_EDIT,
})

_BRANCH_LABELS = {
    BRANCH_INITIAL: "初稿",
    BRANCH_REGENERATE: "重新生成",
    BRANCH_EDIT: "编辑",
    BRANCH_TRUNCATED: "截断保留",
}


def branch_label(kind: str) -> str:
    return _BRANCH_LABELS.get((kind or "").strip(), "历史")


def filter_display_versions(versions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """气泡下可切换的版本（不含截断保留）。"""
    return [v for v in versions if (v.get("branch_kind") or BRANCH_INITIAL) in DISPLAY_VERSION_BRANCHES]


def enrich_version_fields(
    versions: list[dict[str, Any]],
    active_version_index: int | None = None,
) -> dict[str, Any] | None:
    """为 history / SSE 附加统一展示字段：version_pos（1 起）、version_total。"""
    display = filter_display_versions(versions)
    if len(display) <= 1:
        return None
    avi: int | None = None
    if active_version_index is not None:
        try:
            avi = int(active_version_index)
        except (TypeError, ValueError):
            avi = None
    if avi is None or not any(int(v["version_index"]) == avi for v in display):
        avi = int(display[-1]["version_index"])
    pos_1 = 1
    for i, v in enumerate(display):
        if int(v["version_index"]) == avi:
            pos_1 = i + 1
            break
    return {
        "versions": display,
        "active_version_index": avi,
        "version_pos": pos_1,
        "version_total": len(display),
    }


def set_message_active_version_sqlite(
    con: sqlite3.Connection,
    session_id: str,
    anchor_seq: int,
    version_index: int,
) -> None:
    con.execute(
        "UPDATE chat_messages SET active_version_index=? WHERE session_id=? AND seq=?",
        (int(version_index), session_id, anchor_seq),
    )


def version_row_to_dict(r: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    m = dict(r) if not isinstance(r, dict) else r
    out: dict[str, Any] = {
        "version_index": int(m["version_index"]),
        "role": str(m.get("role") or ""),
        "content": str(m.get("content") or ""),
        "branch_kind": str(m.get("branch_kind") or BRANCH_INITIAL),
        "branch_label": branch_label(str(m.get("branch_kind") or "")),
        "created_at_ms": int(m.get("created_at_ms") or 0),
    }
    mid = m.get("message_id")
    if mid is not None and str(mid).strip():
        out["message_id"] = str(mid).strip()
    v = m.get("vote")
    if v is not None:
        try:
            iv = int(v)
            if iv in (-1, 0, 1):
                out["vote"] = iv
        except (TypeError, ValueError):
            pass
    return out


def init_versions_schema(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_message_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            anchor_seq INTEGER NOT NULL,
            version_index INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            message_id TEXT,
            vote INTEGER,
            branch_kind TEXT NOT NULL,
            created_at_ms INTEGER NOT NULL,
            UNIQUE(session_id, anchor_seq, version_index)
        )
        """,
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chat_msg_versions_anchor
        ON chat_message_versions(session_id, anchor_seq)
        """,
    )
    con.commit()


def _next_version_index(con: sqlite3.Connection, session_id: str, anchor_seq: int) -> int:
    row = con.execute(
        "SELECT COALESCE(MAX(version_index), -1) AS m FROM chat_message_versions"
        " WHERE session_id=? AND anchor_seq=?",
        (session_id, anchor_seq),
    ).fetchone()
    return int(row["m"]) + 1 if row else 0


def _last_version_matches(con: sqlite3.Connection, session_id: str, anchor_seq: int, row: dict[str, Any]) -> bool:
    last = con.execute(
        "SELECT content, message_id, vote, role FROM chat_message_versions"
        " WHERE session_id=? AND anchor_seq=? ORDER BY version_index DESC LIMIT 1",
        (session_id, anchor_seq),
    ).fetchone()
    if not last:
        return False
    lm = dict(last)
    if str(lm.get("role")) != str(row.get("role")):
        return False
    if str(lm.get("content") or "") != str(row.get("content") or ""):
        return False
    if str(lm.get("message_id") or "") != str(row.get("message_id") or ""):
        return False
    lv, rv = lm.get("vote"), row.get("vote")
    try:
        lvi = int(lv) if lv is not None else None
    except (TypeError, ValueError):
        lvi = None
    try:
        rvi = int(rv) if rv is not None else None
    except (TypeError, ValueError):
        rvi = None
    return lvi == rvi


def snapshot_sqlite_message(
    con: sqlite3.Connection,
    session_id: str,
    anchor_seq: int,
    *,
    branch_kind: str,
) -> int | None:
    """将 chat_messages 当前行写入 versions；返回 version_index。"""
    row = con.execute(
        "SELECT role, content, message_id, vote, created_at_ms FROM chat_messages"
        " WHERE session_id=? AND seq=?",
        (session_id, anchor_seq),
    ).fetchone()
    if not row:
        return None
    rmap = dict(row)
    snap = {
        "role": rmap["role"],
        "content": rmap["content"],
        "message_id": rmap.get("message_id"),
        "vote": rmap.get("vote"),
    }
    if _last_version_matches(con, session_id, anchor_seq, snap):
        last_kind = con.execute(
            "SELECT branch_kind FROM chat_message_versions"
            " WHERE session_id=? AND anchor_seq=? ORDER BY version_index DESC LIMIT 1",
            (session_id, anchor_seq),
        ).fetchone()
        if last_kind and str(last_kind["branch_kind"]) == str(branch_kind):
            return _next_version_index(con, session_id, anchor_seq) - 1
    vi = _next_version_index(con, session_id, anchor_seq)
    now = int(rmap.get("created_at_ms") or time.time() * 1000)
    con.execute(
        """
        INSERT INTO chat_message_versions(
            session_id, anchor_seq, version_index, role, content,
            message_id, vote, branch_kind, created_at_ms
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            session_id,
            anchor_seq,
            vi,
            str(rmap["role"]),
            str(rmap["content"] or ""),
            rmap.get("message_id"),
            rmap.get("vote"),
            branch_kind,
            now,
        ),
    )
    return vi


def snapshot_sqlite_from_seq(
    con: sqlite3.Connection,
    session_id: str,
    from_seq: int,
    *,
    branch_kind: str,
) -> None:
    rows = con.execute(
        "SELECT seq FROM chat_messages WHERE session_id=? AND seq>=? ORDER BY seq ASC",
        (session_id, from_seq),
    ).fetchall()
    for r in rows:
        snapshot_sqlite_message(con, session_id, int(r["seq"]), branch_kind=branch_kind)


def insert_sqlite_version(
    con: sqlite3.Connection,
    session_id: str,
    anchor_seq: int,
    *,
    role: str,
    content: str,
    message_id: str | None,
    vote: int | None,
    branch_kind: str,
    created_at_ms: int | None = None,
) -> int:
    vi = _next_version_index(con, session_id, anchor_seq)
    now = created_at_ms if created_at_ms is not None else int(time.time() * 1000)
    con.execute(
        """
        INSERT INTO chat_message_versions(
            session_id, anchor_seq, version_index, role, content,
            message_id, vote, branch_kind, created_at_ms
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            session_id,
            anchor_seq,
            vi,
            role,
            content,
            message_id,
            vote,
            branch_kind,
            now,
        ),
    )
    return vi


def list_sqlite_versions(
    con: sqlite3.Connection,
    session_id: str,
    anchor_seq: int,
) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT version_index, role, content, message_id, vote, branch_kind, created_at_ms"
        " FROM chat_message_versions WHERE session_id=? AND anchor_seq=?"
        " ORDER BY version_index ASC",
        (session_id, anchor_seq),
    ).fetchall()
    return [version_row_to_dict(r) for r in rows]


def active_version_index_sqlite(
    con: sqlite3.Connection,
    session_id: str,
    anchor_seq: int,
) -> int:
    versions = list_sqlite_versions(con, session_id, anchor_seq)
    if not versions:
        return 0
    stored = con.execute(
        "SELECT active_version_index FROM chat_messages WHERE session_id=? AND seq=?",
        (session_id, anchor_seq),
    ).fetchone()
    if stored is not None and stored["active_version_index"] is not None:
        try:
            vi = int(stored["active_version_index"])
            if any(int(v["version_index"]) == vi for v in versions):
                return vi
        except (TypeError, ValueError):
            pass
    cur = con.execute(
        "SELECT role, content, message_id, vote FROM chat_messages"
        " WHERE session_id=? AND seq=?",
        (session_id, anchor_seq),
    ).fetchone()
    if not cur:
        return versions[-1]["version_index"]
    cm = dict(cur)
    for v in reversed(versions):
        if (
            v.get("role") == cm.get("role")
            and str(v.get("content") or "") == str(cm.get("content") or "")
            and str(v.get("message_id") or "") == str(cm.get("message_id") or "")
        ):
            vv = v.get("vote")
            cv = cm.get("vote")
            try:
                vvi = int(vv) if vv is not None else None
            except (TypeError, ValueError):
                vvi = None
            try:
                cvi = int(cv) if cv is not None else None
            except (TypeError, ValueError):
                cvi = None
            if vvi == cvi:
                vi = int(v["version_index"])
                set_message_active_version_sqlite(con, session_id, anchor_seq, vi)
                return vi
    vi = int(versions[-1]["version_index"])
    set_message_active_version_sqlite(con, session_id, anchor_seq, vi)
    return vi


def activate_sqlite_version(
    con: sqlite3.Connection,
    session_id: str,
    anchor_seq: int,
    version_index: int,
) -> dict[str, Any] | None:
    row = con.execute(
        "SELECT version_index, role, content, message_id, vote, branch_kind, created_at_ms"
        " FROM chat_message_versions WHERE session_id=? AND anchor_seq=? AND version_index=?",
        (session_id, anchor_seq, version_index),
    ).fetchone()
    if not row:
        return None
    v = version_row_to_dict(row)
    con.execute(
        """
        UPDATE chat_messages
        SET role=?, content=?, message_id=?, vote=?, created_at_ms=?, active_version_index=?
        WHERE session_id=? AND seq=?
        """,
        (
            v["role"],
            v["content"],
            v.get("message_id"),
            v.get("vote"),
            v["created_at_ms"],
            int(version_index),
            session_id,
            anchor_seq,
        ),
    )
    return v


def set_vote_sqlite_by_message_id(
    con: sqlite3.Connection,
    session_id: str,
    message_id: str,
    vote: int,
) -> bool:
    mid = (message_id or "").strip()
    if not mid:
        return False
    n = 0
    cur = con.execute(
        "UPDATE chat_message_versions SET vote=? WHERE session_id=? AND message_id=?",
        (vote, session_id, mid),
    )
    n += cur.rowcount
    cur2 = con.execute(
        "UPDATE chat_messages SET vote=? WHERE session_id=? AND message_id=? AND role='assistant'",
        (vote, session_id, mid),
    )
    n += cur2.rowcount
    return n > 0
