"""
E03 — FastAPI HTTP 服务：`GET /health`、`POST /chat`（SSE 流式默认）、`POST /ingest`（KM-304）。

`GET /` 提供 Web 聊天页。**反代 404 常见原因**：(1) 页面在子路径如 `https://域名/knowme/` 但 `fetch('/chat')` 打到域名根 `/chat`，nginx 未转发 → 设 `KNOW_ME_HTTP_BROWSER_PREFIX=/knowme`；(2) nginx 把**完整**路径转到本进程（未剥离）→ 再设 `KNOW_ME_HTTP_ROOT_PATH=/knowme` 做 ASGI 挂载（可与前者同值）。若 nginx 用 `location /knowme/ { proxy_pass http://127.0.0.1:8000/; }` **已剥离**前缀，通常**只**需 `KNOW_ME_HTTP_BROWSER_PREFIX`。
也可由 `uvicorn know_me.api.app:app --host 0.0.0.0` 启动（`0.0.0.0` 方可被局域网 IP 访问）；启动时尝试自 CWD 向上加载 `.env`（与 CLI 行为一致）。
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import iterate_in_threadpool

from know_me import __version__
from know_me.agent.agent_chat import iter_agent_chat_events, run_agent_chat_blocking
from know_me.agent.prompts_agent import SESSION_OPENING_ASK_IDENTITY
from know_me.observability.feedback_store import append_feedback
from know_me.index.pipeline import build_index
from know_me.core.settings import IndexSettings
from know_me.agent.sessions import ChatSessionStore

log = logging.getLogger(__name__)

_session_store: ChatSessionStore | None = None
_WEB_UI_DIR = Path(__file__).resolve().parent.parent / "web_ui"


def _bootstrap_dotenv() -> None:
    here = Path.cwd().resolve()
    for d in [here, *list(here.parents)[:16]]:
        candidate = d / ".env"
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return


def _normalize_http_root(raw: str) -> str:
    """供挂载与前端 API 前缀；空表示根路径。例：`/knowme`（无尾部 `/`）。"""
    s = (raw or "").strip().rstrip("/")
    if not s or s == "/":
        return ""
    return s if s.startswith("/") else "/" + s


def _inject_web_index(http_root_browser: str) -> str:
    """在 `index.html` 中注入 `window.__KM_HTTP_ROOT`，便于子路径下 `fetch` 命中本服务。"""
    index = _WEB_UI_DIR / "index.html"
    if not index.is_file():
        raise FileNotFoundError(str(index))
    html = index.read_text(encoding="utf-8")
    token = "%%KNOW_ME_HTTP_ROOT%%"
    if token not in html:
        log.warning("web_ui/index.html 缺少占位符 %s，子路径下 API 可能 404", token)
        return html
    return html.replace(token, json.dumps(http_root_browser or ""), 1)


def get_session_store() -> ChatSessionStore:
    global _session_store
    if _session_store is None:
        _session_store = ChatSessionStore(IndexSettings.from_env().chat_history_max_turns)
    return _session_store


@asynccontextmanager
async def lifespan(_: FastAPI):
    _bootstrap_dotenv()
    yield


_bootstrap_dotenv()
_HTTP_MOUNT = _normalize_http_root(os.environ.get("KNOW_ME_HTTP_ROOT_PATH", ""))
# 浏览器地址栏在子路径下时，API 须带此前缀；nginx 已剥离 upstream 路径时仍须设置，否则 fetch('/chat') 会打到错误路径
_HTTP_BROWSER_PREFIX = _normalize_http_root(os.environ.get("KNOW_ME_HTTP_BROWSER_PREFIX", "")) or _HTTP_MOUNT

inner = FastAPI(title="Know Me", version=__version__, lifespan=lifespan)
inner.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SessionRequest(BaseModel):
    """复用已有 session_id 时若服务端历史仍为空，则返回同 id 与开场白；否则仅返回 session_id。"""

    session_id: str | None = Field(None, description="可选；传入则尽量复用该会话")


class ChatRequest(BaseModel):
    session_id: str | None = Field(None, description="会话 id；省略则创建新会话")
    message: str = Field(..., min_length=1, description="用户本轮输入")
    stream: bool = Field(True, description="true 时以 SSE 流式返回")
    top_k: int | None = Field(None, ge=1, le=50, description="覆盖检索 Top-K")
    preface_shown: bool = Field(
        False,
        description="为 true 表示客户端已通过 /session 等方式展示过开场白；首条消息且历史为空时 SSE 不再附带 session.opening",
    )


class FeedbackRequest(BaseModel):
    message_id: str = Field(..., min_length=8, description="与 `/chat` 返回的 message_id 一致")
    vote: int = Field(..., ge=-1, le=1, description="-1 点踩，0 中性，1 点赞")
    comment: str | None = Field(None, max_length=2000, description="可选短评")


def _require_models(settings: IndexSettings) -> None:
    if not settings.openai_embed_model.strip():
        raise HTTPException(status_code=503, detail="未配置 KNOW_ME_OPENAI_EMBED_MODEL")
    if not settings.openai_chat_model.strip():
        raise HTTPException(status_code=503, detail="未配置 KNOW_ME_OPENAI_CHAT_MODEL")


@inner.get("/", include_in_schema=False)
async def web_chat_ui() -> HTMLResponse:
    """内置 Web 聊天页；注入 `KNOW_ME_HTTP_ROOT_PATH` 供前端拼接 `/session`、`/chat` 等。"""
    try:
        body = _inject_web_index(_HTTP_BROWSER_PREFIX)
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="Web UI 未找到：缺少 know_me/web_ui/index.html") from None
    return HTMLResponse(body, media_type="text/html; charset=utf-8")


@inner.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "version": __version__, "http_root": _HTTP_MOUNT or "", "browser_api_prefix": _HTTP_BROWSER_PREFIX or ""}


@inner.post("/session")
async def create_session(body: SessionRequest = SessionRequest()) -> dict[str, Any]:
    """新建或复用会话；仅当该会话尚无消息历史时返回 `opening`，供首屏欢迎。"""
    store = get_session_store()
    sid = store.ensure_session(body.session_id)
    hist = store.history(sid)
    opening = SESSION_OPENING_ASK_IDENTITY if len(hist) == 0 else None
    return {"session_id": sid, "opening": opening}


@inner.post("/ingest")
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


@inner.post("/feedback")
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


@inner.post("/chat", response_model=None)
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
                if not hist and not body.preface_shown:
                    sess_ev["opening"] = SESSION_OPENING_ASK_IDENTITY
                yield f"data: {json.dumps(sess_ev, ensure_ascii=False)}\n\n".encode("utf-8")
                try:
                    for ev in iter_agent_chat_events(
                        settings,
                        hist,
                        body.message,
                        top_k=body.top_k,
                        message_id=mid,
                        preface_shown=body.preface_shown,
                    ):
                        if ev.get("type") == "done":
                            st["full"] = str(ev.get("answer_stored") or ev.get("answer") or "")
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

    result = run_agent_chat_blocking(
        settings,
        hist,
        body.message,
        top_k=body.top_k,
        message_id=mid,
        preface_shown=body.preface_shown,
    )
    if not result.get("error"):
        ans = str(result.get("answer_stored") or result.get("answer") or "")
        if ans.strip():
            store.append_turn(sid, body.message, ans)
    if not str(result.get("message_id") or "").strip():
        result = {**result, "message_id": mid}
    result.pop("answer_stored", None)
    out: dict[str, Any] = {"session_id": sid, **result}
    if not hist and not body.preface_shown:
        out["opening"] = SESSION_OPENING_ASK_IDENTITY
    return JSONResponse(out)


if _HTTP_MOUNT:
    app = FastAPI(title="Know Me", version=__version__)

    @app.get("/", include_in_schema=False)
    async def _redirect_to_mount() -> RedirectResponse:
        return RedirectResponse(url=f"{_HTTP_MOUNT}/", status_code=307)

    app.mount(_HTTP_MOUNT, inner)
else:
    app = inner
