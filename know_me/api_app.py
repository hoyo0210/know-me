"""
E03 — FastAPI HTTP 服务：`GET /health`、`POST /chat`（SSE 流式默认）、`POST /ingest`（KM-304）。

也可由 `uvicorn know_me.api_app:app` 直接启动；启动时尝试自 CWD 向上加载 `.env`（与 CLI 行为一致）。
"""

from __future__ import annotations

import json
import logging
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import iterate_in_threadpool

from know_me import __version__
from know_me.agent_chat import iter_agent_chat_events, run_agent_chat_blocking
from know_me.feedback_store import append_feedback
from know_me.pipeline import build_index
from know_me.settings import IndexSettings
from know_me.sessions import ChatSessionStore

log = logging.getLogger(__name__)

_session_store: ChatSessionStore | None = None


def _bootstrap_dotenv() -> None:
    here = Path.cwd().resolve()
    for d in [here, *list(here.parents)[:16]]:
        candidate = d / ".env"
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return


def get_session_store() -> ChatSessionStore:
    global _session_store
    if _session_store is None:
        _session_store = ChatSessionStore(IndexSettings.from_env().chat_history_max_turns)
    return _session_store


@asynccontextmanager
async def lifespan(_: FastAPI):
    _bootstrap_dotenv()
    yield


app = FastAPI(title="Know Me", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    session_id: str | None = Field(None, description="会话 id；省略则创建新会话")
    message: str = Field(..., min_length=1, description="用户本轮输入")
    stream: bool = Field(True, description="true 时以 SSE 流式返回")
    top_k: int | None = Field(None, ge=1, le=50, description="覆盖检索 Top-K")


class FeedbackRequest(BaseModel):
    message_id: str = Field(..., min_length=8, description="与 `/chat` 返回的 message_id 一致")
    vote: int = Field(..., ge=-1, le=1, description="-1 点踩，0 中性，1 点赞")
    comment: str | None = Field(None, max_length=2000, description="可选短评")


def _require_models(settings: IndexSettings) -> None:
    if not settings.openai_embed_model.strip():
        raise HTTPException(status_code=503, detail="未配置 KNOW_ME_OPENAI_EMBED_MODEL")
    if not settings.openai_chat_model.strip():
        raise HTTPException(status_code=503, detail="未配置 KNOW_ME_OPENAI_CHAT_MODEL")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "version": __version__}


@app.post("/ingest")
async def ingest(authorization: str | None = Header(None, alias="Authorization")) -> dict[str, Any]:
    settings = IndexSettings.from_env()
    expected = settings.ingest_api_key
    if not expected:
        raise HTTPException(status_code=503, detail="未配置 KNOW_ME_INGEST_API_KEY，入库接口已禁用")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="需要 Authorization: Bearer <token>")
    token = authorization.split(" ", 1)[1].strip()
    if len(token) != len(expected) or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="入库密钥无效")
    try:
        stats = build_index(settings, reset=False)
    except Exception as e:
        log.exception("ingest 失败：%s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return stats


@app.post("/feedback")
async def post_feedback(body: FeedbackRequest) -> dict[str, bool]:
    """E05 KM-503：将反馈追加写入 `KNOW_ME_FEEDBACK_LOG`（须启用 `KNOW_ME_FEEDBACK_ENABLED`）。"""
    settings = IndexSettings.from_env()
    if not settings.feedback_enabled:
        raise HTTPException(status_code=503, detail="反馈入口未启用（设置 KNOW_ME_FEEDBACK_ENABLED=1）")
    append_feedback(
        settings.feedback_log_path,
        {
            "ts_ms": int(time.time() * 1000),
            "message_id": body.message_id.strip(),
            "vote": body.vote,
            "comment": (body.comment.strip() if isinstance(body.comment, str) else "") or None,
        },
    )
    return {"ok": True}


@app.post("/chat", response_model=None)
async def chat(body: ChatRequest) -> StreamingResponse | JSONResponse:
    settings = IndexSettings.from_env()
    _require_models(settings)
    store = get_session_store()
    sid = store.ensure_session(body.session_id)
    hist = store.history(sid)
    mid = uuid.uuid4().hex

    if body.stream:

        async def async_sse():
            st: dict[str, Any] = {"full": "", "err": False}

            def byte_chunks():
                sess_ev: dict[str, Any] = {"type": "session", "session_id": sid, "message_id": mid}
                if settings.disclaimer_text.strip():
                    sess_ev["disclaimer"] = settings.disclaimer_text.strip()
                yield f"data: {json.dumps(sess_ev, ensure_ascii=False)}\n\n".encode("utf-8")
                try:
                    for ev in iter_agent_chat_events(
                        settings,
                        hist,
                        body.message,
                        top_k=body.top_k,
                        message_id=mid,
                    ):
                        if ev.get("type") == "done":
                            st["full"] = str(ev.get("answer") or "")
                        if ev.get("type") == "error":
                            st["err"] = True
                        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8")
                except Exception as e:
                    st["err"] = True
                    log.exception("chat stream 失败：%s", e)
                    err_ev = {"type": "error", "message": str(e)}
                    yield f"data: {json.dumps(err_ev, ensure_ascii=False)}\n\n".encode("utf-8")

            async for chunk in iterate_in_threadpool(byte_chunks()):
                yield chunk
            if not st["err"] and str(st["full"]).strip():
                store.append_turn(sid, body.message, str(st["full"]))

        return StreamingResponse(async_sse(), media_type="text/event-stream")

    result = run_agent_chat_blocking(settings, hist, body.message, top_k=body.top_k, message_id=mid)
    if not result.get("error"):
        ans = str(result.get("answer") or "")
        if ans.strip():
            store.append_turn(sid, body.message, ans)
    if not str(result.get("message_id") or "").strip():
        result = {**result, "message_id": mid}
    return JSONResponse({"session_id": sid, **result})
