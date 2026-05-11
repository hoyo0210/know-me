"""
E03 — 进程内多轮会话存储（KM-301）。

仅保留 `user` / `assistant` 文本轮次，便于与 OpenAI 风格消息对齐；工具调用细节不持久化。
生产环境可替换为 Redis 等外部存储。
"""

from __future__ import annotations

import threading
import uuid
from typing import Any


class ChatSessionStore:
    """线程安全的 session_id → 消息列表。"""

    def __init__(self, max_turns: int) -> None:
        self._max_turns = max(1, max_turns)
        self._lock = threading.Lock()
        self._sessions: dict[str, list[dict[str, Any]]] = {}

    def ensure_session(self, session_id: str | None) -> str:
        """若未传 session_id 则新建 UUID；已存在则复用。"""
        sid = (session_id or "").strip() or str(uuid.uuid4())
        with self._lock:
            self._sessions.setdefault(sid, [])
        return sid

    def history(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._sessions.get(session_id, []))

    def append_turn(self, session_id: str, user_content: str, assistant_content: str) -> None:
        """追加一轮 user + assistant，并按配置裁剪。"""
        with self._lock:
            hist = self._sessions.setdefault(session_id, [])
            hist.append({"role": "user", "content": user_content})
            hist.append({"role": "assistant", "content": assistant_content})
            self._trim(hist)

    def _trim(self, hist: list[dict[str, Any]]) -> None:
        max_msgs = self._max_turns * 2
        if len(hist) > max_msgs:
            del hist[0 : len(hist) - max_msgs]
