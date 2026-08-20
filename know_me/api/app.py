"""
E03 — FastAPI HTTP 服务：`GET /health`、`POST /session`（新会话可按访客信息用**模板**生成开场白）、`GET /session/{id}/history`、`PUT /session/{id}/messages/{seq}/active-version`（仅切换展示版本）、`POST /chat`（SSE 默认；支持 `regenerate_assistant_seq` / `edit_user_seq` 分支写库）、`POST /ingest`（KM-304）。

`GET /` 提供 Web 聊天页，`GET /resume` 提供静态简历页（均注入 `KNOW_ME_HTTP_BROWSER_PREFIX`）。**反代 404 常见原因**：(1) 页面在子路径如 `https://域名/knowme/` 但 `fetch('/chat')` 打到域名根 `/chat`，nginx 未转发 → 设 `KNOW_ME_HTTP_BROWSER_PREFIX=/knowme`；(2) nginx 把**完整**路径转到本进程（未剥离）→ 再设 `KNOW_ME_HTTP_ROOT_PATH=/knowme` 做 ASGI 挂载（可与前者同值）。若 nginx 用 `location /knowme/ { proxy_pass http://127.0.0.1:8000/; }` **已剥离**前缀，通常**只**需 `KNOW_ME_HTTP_BROWSER_PREFIX`。
也可由 `uvicorn know_me.api.app:app --host 0.0.0.0` 启动（`0.0.0.0` 方可被局域网 IP 访问）；启动时尝试自 CWD 向上加载 `.env`（与 CLI 行为一致）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import os
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from know_me import __version__

_SSE_KEEPALIVE = b": keepalive\n\n"


async def iterate_sync_gen_with_sse_keepalive(
    gen_factory: Callable[[], Iterator[bytes]],
    *,
    ping_interval_sec: float = 12.0,
) -> AsyncIterator[bytes]:
    """
    在线程池跑同步 SSE 生成器；阻塞过久时发 keepalive，避免 nginx/浏览器因长时间无字节断连。
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes | None | BaseException] = asyncio.Queue()

    def worker() -> None:
        try:
            for chunk in gen_factory():
                loop.call_soon_threadsafe(queue.put_nowait, chunk)
        except BaseException as exc:
            loop.call_soon_threadsafe(queue.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    loop.run_in_executor(None, worker)
    while True:
        try:
            item = await asyncio.wait_for(queue.get(), timeout=ping_interval_sec)
        except asyncio.TimeoutError:
            yield _SSE_KEEPALIVE
            continue
        if item is None:
            break
        if isinstance(item, BaseException):
            raise item
        yield item


from know_me.agent.agent_chat import iter_agent_chat_events, run_agent_chat_blocking
from know_me.agent.context_window import refresh_conversation_summary_if_needed
from know_me.agent.recruiter_job import is_credible_recruiter_job_title
from know_me.agent.session_opening_gen import fallback_session_opening
from know_me.observability.feedback_store import append_feedback
from know_me.index.pipeline import build_index
from know_me.core.settings import IndexSettings
from know_me.agent.sessions import make_chat_session_store

log = logging.getLogger(__name__)

_session_store: Any = None
_WEB_UI_DIR = Path(__file__).resolve().parent.parent / "web_ui"
_RESUME_DIST_DIR = _WEB_UI_DIR / "resume_dist"


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


_WEB_HTML_TOKEN = "%%KNOW_ME_HTTP_ROOT%%"
_WEB_RESUME_TOKEN = "%%KNOW_ME_RESUME_URL%%"
_DEFAULT_RESUME_BROWSER_URL = ""


def _inject_web_html(filename: str, http_root_browser: str) -> str:
    """读取 `web_ui/{filename}` 并注入 `window.__KM_HTTP_ROOT`（占位符与 index 一致）。"""
    path = _WEB_UI_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(str(path))
    html = path.read_text(encoding="utf-8")
    if _WEB_HTML_TOKEN not in html:
        log.warning("web_ui/%s 缺少占位符 %s，子路径下 API 可能 404", filename, _WEB_HTML_TOKEN)
        return html
    return html.replace(_WEB_HTML_TOKEN, json.dumps(http_root_browser or ""), 1)


def _resume_browser_url() -> str:
    raw = (os.environ.get("KNOW_ME_RESUME_BROWSER_URL") or "").strip()
    return raw


def _inject_web_index(http_root_browser: str) -> str:
    """在 `index.html` 中注入浏览器用根路径与简历站外链。"""
    path = _WEB_UI_DIR / "index.html"
    if not path.is_file():
        raise FileNotFoundError(str(path))
    html = path.read_text(encoding="utf-8")
    if _WEB_HTML_TOKEN in html:
        html = html.replace(_WEB_HTML_TOKEN, json.dumps(http_root_browser or ""), 1)
    else:
        log.warning("web_ui/index.html 缺少占位符 %s", _WEB_HTML_TOKEN)
    if _WEB_RESUME_TOKEN in html:
        html = html.replace(_WEB_RESUME_TOKEN, json.dumps(_resume_browser_url()), 1)
    else:
        log.warning("web_ui/index.html 缺少占位符 %s，简历按钮可能指向错误域名", _WEB_RESUME_TOKEN)
    return html


def _inject_resume_dist(http_root_browser: str) -> str:
    """读取 `web_ui/resume_dist/index.html`（Vue 构建产物）并注入 HTTP 根路径。"""
    path = _RESUME_DIST_DIR / "index.html"
    if not path.is_file():
        raise FileNotFoundError(str(path))
    html = path.read_text(encoding="utf-8")
    if _WEB_HTML_TOKEN not in html:
        log.warning("resume_dist/index.html 缺少占位符 %s，子路径下链接可能 404", _WEB_HTML_TOKEN)
        return html
    return html.replace(_WEB_HTML_TOKEN, json.dumps(http_root_browser or ""), 1)


def get_session_store() -> Any:
    global _session_store
    if _session_store is None:
        _session_store = make_chat_session_store(IndexSettings.from_env())
    return _session_store


def _version_ui_payload(store: Any, session_id: str, anchor_seq: int) -> dict[str, Any] | None:
    """与 GET history 单条消息一致的版本展示字段，供 SSE done 与前端计数对齐。"""
    hist = store.history(session_id)
    msg = next((m for m in hist if int(m.get("seq", -1)) == int(anchor_seq)), None)
    if not msg or not msg.get("versions"):
        return None
    keys = ("versions", "active_version_index", "version_pos", "version_total")
    return {k: msg[k] for k in keys if k in msg}


def _attach_version_ui_to_done(store: Any, session_id: str, done: dict[str, Any], assistant_seq: int | None) -> None:
    if assistant_seq is None:
        return
    vp = _version_ui_payload(store, session_id, int(assistant_seq))
    if vp:
        done.update(vp)


def _last_user_message_text(hist: list[dict[str, Any]]) -> str:
    """取历史中最后一条非空 user 正文（截断/编辑后用于再生成）。"""
    for m in reversed(hist):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            t = m["content"].strip()
            if t:
                return t
    return ""


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
    """复用已有 session_id；尚无历史且提供访客称呼与身份时返回模板生成的 `opening`。"""

    session_id: str | None = Field(None, description="可选；传入则尽量复用该会话")
    viewer_display_name: str | None = Field(None, max_length=64, description="与 Web 欢迎页一致，用于生成开场白")
    viewer_role: str | None = Field(None, max_length=64, description="访客身份，用于生成开场白")
    recruiter_job_title: str | None = Field(
        None,
        max_length=256,
        description="招聘方提供的岗位名称或职责要点（选填；有则写入会话并视为岗位已明确）",
    )
    recruiter_contact: str | None = Field(
        None,
        max_length=128,
        description="招聘方联系方式（选填，如手机/微信/邮箱）",
    )


class ChatRequest(BaseModel):
    session_id: str | None = Field(None, description="会话 id；省略则创建新会话")
    message: str = Field(
        "",
        max_length=100_000,
        description="新消息正文；edit_user_seq 时为修改后的用户文案；仅 regenerate_assistant_seq 时可留空",
    )
    stream: bool = Field(True, description="true 时以 SSE 流式返回")
    top_k: int | None = Field(None, ge=1, le=50, description="覆盖检索 Top-K")
    preface_shown: bool = Field(
        False,
        description="为 true 表示客户端已展示过 `/session` 返回的开场白；首轮 POST /chat 须与 `session_opening_for_context` 一致",
    )
    regenerate_assistant_seq: int | None = Field(
        None,
        ge=0,
        description="删除该 seq 的 assistant 及之后所有消息，再用紧前 user 重生成（写入同一会话历史）",
    )
    edit_user_seq: int | None = Field(
        None,
        ge=0,
        description="将指定 seq 的 user 替换为 message，并删除其后的历史，再生成 assistant",
    )
    viewer_display_name: str | None = Field(
        None,
        max_length=64,
        description="访客称呼（Web 欢迎页）；并入 Agent system 提示",
    )
    viewer_role: str | None = Field(
        None,
        max_length=64,
        description="访客身份定位（如人事经理）；并入 Agent system 提示",
    )
    session_opening_for_context: str | None = Field(
        None,
        max_length=4096,
        description="客户端已展示的开场白全文，首轮须与 /session.opening 一致并写入 LLM system",
    )


class FeedbackRequest(BaseModel):
    session_id: str | None = Field(
        None,
        description="会话 id；与点赞/点踩写入 `chat_messages.vote` 时一并提供（与 GET /session/.../history 同源）",
    )
    message_id: str = Field(..., min_length=8, description="与 `/chat` SSE `done.message_id` 一致")
    vote: int = Field(..., ge=-1, le=1, description="-1 点踩，0 中性，1 点赞")
    comment: str | None = Field(None, max_length=2000, description="可选短评")


class ActiveVersionRequest(BaseModel):
    version_index: int = Field(..., ge=0, description="要切换到的版本下标（见 history 中 messages[].versions）")


def _require_models(settings: IndexSettings) -> None:
    if not settings.openai_embed_model.strip():
        raise HTTPException(status_code=503, detail="未配置 KNOW_ME_OPENAI_EMBED_MODEL")
    if not settings.openai_chat_model.strip():
        raise HTTPException(status_code=503, detail="未配置 KNOW_ME_OPENAI_CHAT_MODEL")


def _norm_viewer_field(raw: str | None) -> str | None:
    if raw is None:
        return None
    t = raw.strip().replace("\r", " ").replace("\n", " ")
    return t[:64].rstrip() if t else None


def _norm_recruiter_job_field(raw: str | None) -> str | None:
    if raw is None:
        return None
    t = raw.strip().replace("\r", " ").replace("\n", " ")
    return t[:256].rstrip() if t else None


def _norm_recruiter_contact_field(raw: str | None) -> str | None:
    if raw is None:
        return None
    t = raw.strip().replace("\r", " ").replace("\n", " ")
    return t[:128].rstrip() if t else None


def _norm_session_opening_context(raw: str | None) -> str | None:
    if raw is None:
        return None
    t = raw.strip()
    if not t:
        return None
    t = t.replace("\r\n", "\n")
    if len(t) > 4096:
        t = t[:4096].rstrip()
    return t


@inner.get("/resume", include_in_schema=False)
async def web_resume_page() -> HTMLResponse:
    """静态简历页（Vue 构建产物 `web_ui/resume_dist`）；与聊天页共用 HTTP 根路径注入。"""
    try:
        body = _inject_resume_dist(_HTTP_BROWSER_PREFIX)
    except FileNotFoundError:
        raise HTTPException(
            status_code=503,
            detail="简历页未构建：请在 resume-site 目录执行 npm install && npm run build:know-me",
        ) from None
    return HTMLResponse(
        body,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


if (_RESUME_DIST_DIR / "assets").is_dir():
    inner.mount(
        "/resume/assets",
        StaticFiles(directory=_RESUME_DIST_DIR / "assets"),
        name="resume_assets",
    )


@inner.get("/", include_in_schema=False)
async def web_chat_ui() -> HTMLResponse:
    """内置 Web 聊天页；注入 `KNOW_ME_HTTP_ROOT_PATH` 供前端拼接 `/session`、`/chat` 等。"""
    try:
        body = _inject_web_index(_HTTP_BROWSER_PREFIX)
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="Web UI 未找到：缺少 know_me/web_ui/index.html") from None
    return HTMLResponse(
        body,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@inner.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "version": __version__, "http_root": _HTTP_MOUNT or "", "browser_api_prefix": _HTTP_BROWSER_PREFIX or ""}


@inner.put("/session/{session_id}/messages/{anchor_seq}/active-version")
async def set_message_active_version(
    session_id: str,
    anchor_seq: int,
    body: ActiveVersionRequest,
) -> dict[str, Any]:
    """仅切换该条消息的展示版本（含对应 message_id / vote），不调用 LLM。"""
    sid = (session_id or "").strip()
    if not sid or len(sid) > 128:
        raise HTTPException(status_code=400, detail="无效的 session_id")
    if anchor_seq < 0:
        raise HTTPException(status_code=400, detail="无效的 anchor_seq")
    store = get_session_store()
    activated = store.activate_message_version(sid, anchor_seq, body.version_index)
    if activated is None:
        raise HTTPException(status_code=404, detail="未找到该消息或版本")
    hist = store.history(sid)
    msg = next((m for m in hist if int(m.get("seq", -1)) == anchor_seq), None)
    if msg is None:
        raise HTTPException(status_code=404, detail="消息不存在")
    return {
        "session_id": sid,
        "anchor_seq": anchor_seq,
        "active_version_index": int(msg.get("active_version_index", body.version_index)),
        "message": msg,
        "activated": activated,
    }


@inner.get("/session/{session_id}/history")
async def get_session_history(session_id: str) -> dict[str, Any]:
    """返回该会话已持久化的 user/assistant 消息列表（按时间顺序），供前端恢复聊天区。"""
    sid = (session_id or "").strip()
    if not sid or len(sid) > 128:
        raise HTTPException(status_code=400, detail="无效的 session_id")
    store = get_session_store()
    hist = store.history(sid)
    vdn, vdr = store.get_session_viewer(sid)
    job, contact = store.get_session_recruiter_context(sid)
    return {
        "session_id": sid,
        "messages": hist,
        "message_count": len(hist),
        "has_history": len(hist) > 0,
        "viewer_display_name": vdn,
        "viewer_role": vdr,
        "recruiter_job_title": job,
        "recruiter_job_title_credible": bool(job and is_credible_recruiter_job_title(job)),
        "recruiter_contact": contact,
    }


@inner.post("/session")
async def create_session(body: SessionRequest = SessionRequest()) -> dict[str, Any]:
    """新建或复用会话。提交称呼、身份；招聘岗位与联系方式选填。尚无消息时用固定模板生成 `opening`（不调 LLM）。"""
    store = get_session_store()
    sid = store.ensure_session(body.session_id)
    hist = store.history(sid)
    n = len(hist)
    vn = _norm_viewer_field(body.viewer_display_name)
    vr = _norm_viewer_field(body.viewer_role)
    job_in = _norm_recruiter_job_field(body.recruiter_job_title)
    contact_in = _norm_recruiter_contact_field(body.recruiter_contact)
    job_db, contact_db = store.get_session_recruiter_context(sid)
    opening: str | None = None

    if job_in:
        store.set_session_recruiter_context(sid, job_in, contact_in if contact_in is not None else contact_db)
        job_db, contact_db = job_in, contact_in if contact_in is not None else contact_db
    elif contact_in is not None and job_db:
        store.set_session_recruiter_context(sid, job_db, contact_in)
        contact_db = contact_in

    if vn and vr:
        sdn, sdr = store.get_session_viewer(sid)
        if not (_norm_viewer_field(sdn) and _norm_viewer_field(sdr)):
            store.set_session_viewer(sid, vn, vr)

    if n == 0 and vn and vr:
        o2 = (fallback_session_opening(vn, vr) or "").strip()
        opening = o2 if o2 else None
        if opening:
            store.append_session_opening(sid, opening)

    hist_out = store.history(sid)
    job_out, contact_out = store.get_session_recruiter_context(sid)
    return {
        "session_id": sid,
        "opening": opening,
        "has_history": len(hist_out) > 0,
        "message_count": len(hist_out),
        "recruiter_job_title": job_out,
        "recruiter_contact": contact_out,
    }


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
async def post_feedback(body: FeedbackRequest) -> dict[str, Any]:
    """将反馈写入会话库（`chat_messages.vote`）及可选 JSONL（`KNOW_ME_FEEDBACK_ENABLED=1`）。"""
    settings = IndexSettings.from_env()
    store = get_session_store()
    mid = body.message_id.strip()
    sid = (body.session_id or "").strip()
    chat_updated = False
    if sid and mid:
        chat_updated = bool(store.set_message_vote(sid, mid, body.vote))
    if settings.feedback_enabled:
        append_feedback(
            settings.feedback_log_path,
            {
                "ts_ms": int(time.time() * 1000),
                "session_id": sid or None,
                "message_id": mid,
                "vote": body.vote,
                "comment": (body.comment.strip() if isinstance(body.comment, str) else "") or None,
            },
        )
    if not chat_updated and not settings.feedback_enabled:
        raise HTTPException(
            status_code=400,
            detail="无法记录反馈：请提供有效的 session_id 以写入聊天记录，或设置 KNOW_ME_FEEDBACK_ENABLED=1 启用 JSONL 日志",
        )
    return {"ok": True, "chat_record_updated": chat_updated}


@inner.post("/chat", response_model=None)
async def chat(body: ChatRequest) -> StreamingResponse | JSONResponse:
    settings = IndexSettings.from_env()
    _require_models(settings)
    store = get_session_store()
    sid = store.ensure_session(body.session_id)
    reg = body.regenerate_assistant_seq
    ed = body.edit_user_seq
    if reg is not None and ed is not None:
        raise HTTPException(status_code=400, detail="不能同时指定 regenerate_assistant_seq 与 edit_user_seq")
    branch_append = False
    if reg is not None:
        if not store.apply_regenerate_at_assistant(sid, reg):
            raise HTTPException(status_code=400, detail="无效的 regenerate_assistant_seq（须为本会话的 assistant 行）")
        branch_append = True
    elif ed is not None:
        edited = body.message.strip()
        if not edited:
            raise HTTPException(status_code=400, detail="编辑用户消息时 message 不能为空")
        if not store.apply_edit_user_message(sid, ed, edited):
            raise HTTPException(status_code=400, detail="无效的 edit_user_seq（须为本会话的 user 行）")
        branch_append = True

    hist = store.history(sid)
    if branch_append:
        um = _last_user_message_text(hist)
        if not um:
            raise HTTPException(status_code=400, detail="截断后没有可用于生成的用户消息")
    else:
        um = body.message.strip()
        if not um:
            raise HTTPException(status_code=400, detail="message 不能为空")

    mid = uuid.uuid4().hex

    b_dn = _norm_viewer_field(body.viewer_display_name)
    b_dr = _norm_viewer_field(body.viewer_role)
    sd_dn, sd_dr = store.get_session_viewer(sid)
    sd_dn = _norm_viewer_field(sd_dn)
    sd_dr = _norm_viewer_field(sd_dr)
    if b_dn and b_dr:
        store.set_session_viewer(sid, b_dn, b_dr)
    v_name = b_dn or sd_dn
    v_role = b_dr or sd_dr
    job_db, contact_db = store.get_session_recruiter_context(sid)
    v_opening = _norm_session_opening_context(body.session_opening_for_context)
    if settings.agent_summary_mode == "lazy":
        refresh_conversation_summary_if_needed(settings, store, sid)
    conv_summary, _ = store.get_conversation_summary(sid)

    if body.stream:

        async def async_sse():
            st: dict[str, Any] = {"full": "", "err": False}
            done_holder: dict[str, Any | None] = {"ev": None}

            def byte_chunks():
                sess_ev: dict[str, Any] = {"type": "session", "session_id": sid, "message_id": mid}
                if settings.disclaimer_text.strip():
                    sess_ev["disclaimer"] = settings.disclaimer_text.strip()
                yield f"data: {json.dumps(sess_ev, ensure_ascii=False)}\n\n".encode("utf-8")
                try:
                    for ev in iter_agent_chat_events(
                        settings,
                        hist,
                        um,
                        top_k=body.top_k,
                        message_id=mid,
                        preface_shown=body.preface_shown,
                        viewer_display_name=v_name,
                        viewer_role=v_role,
                        recruiter_job_title=job_db,
                        recruiter_contact=contact_db,
                        session_opening_for_context=v_opening,
                        conversation_summary=conv_summary,
                    ):
                        if ev.get("type") == "done":
                            st["full"] = str(ev.get("answer_stored") or ev.get("answer") or "")
                            out_done = dict(ev)
                            if not st["err"] and str(st["full"]).strip():
                                if branch_append:
                                    asst_seq = store.append_assistant_reply(
                                        sid, str(st["full"]), assistant_message_id=mid
                                    )
                                else:
                                    asst_seq = store.append_turn(
                                        sid, um, str(st["full"]), assistant_message_id=mid
                                    )
                                if asst_seq is not None:
                                    out_done["assistant_seq"] = asst_seq
                                    _attach_version_ui_to_done(store, sid, out_done, asst_seq)
                                if ed is not None:
                                    uvp = _version_ui_payload(store, sid, int(ed))
                                    if uvp:
                                        out_done["user_version_ui"] = uvp
                            yield f"data: {json.dumps(out_done, ensure_ascii=False)}\n\n".encode(
                                "utf-8",
                            )
                            done_holder["ev"] = None
                            if settings.agent_summary_mode == "after_turn":

                                def _bg_summary() -> None:
                                    try:
                                        refresh_conversation_summary_if_needed(
                                            settings, store, sid,
                                        )
                                    except Exception:
                                        log.exception("更新会话摘要失败 session=%s", sid[:8])

                                threading.Thread(target=_bg_summary, daemon=True).start()
                            continue
                        if ev.get("type") == "error":
                            st["err"] = True
                            done_holder["ev"] = None
                        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8")
                except Exception as e:
                    st["err"] = True
                    done_holder["ev"] = None
                    log.exception("chat stream 失败：%s", e)
                    err_ev = {"type": "error", "message": str(e)}
                    yield f"data: {json.dumps(err_ev, ensure_ascii=False)}\n\n".encode("utf-8")

            async for chunk in iterate_sync_gen_with_sse_keepalive(byte_chunks):
                yield chunk

        return StreamingResponse(async_sse(), media_type="text/event-stream")

    result = run_agent_chat_blocking(
        settings,
        hist,
        um,
        top_k=body.top_k,
        message_id=mid,
        preface_shown=body.preface_shown,
        viewer_display_name=v_name,
        viewer_role=v_role,
        recruiter_job_title=job_db,
        recruiter_contact=contact_db,
        session_opening_for_context=v_opening,
        conversation_summary=conv_summary,
    )
    if not result.get("error"):
        ans = str(result.get("answer_stored") or result.get("answer") or "")
        if ans.strip():
            if branch_append:
                asst_seq = store.append_assistant_reply(sid, ans, assistant_message_id=mid)
            else:
                asst_seq = store.append_turn(sid, um, ans, assistant_message_id=mid)
            if asst_seq is not None:
                result["assistant_seq"] = asst_seq
                _attach_version_ui_to_done(store, sid, result, asst_seq)
                if ed is not None:
                    uvp = _version_ui_payload(store, sid, int(ed))
                    if uvp:
                        result["user_version_ui"] = uvp
                if settings.agent_summary_mode == "after_turn":
                    try:
                        refresh_conversation_summary_if_needed(settings, store, sid)
                    except Exception:
                        log.exception("更新会话摘要失败 session=%s", sid[:8])
    if not str(result.get("message_id") or "").strip():
        result = {**result, "message_id": mid}
    result.pop("answer_stored", None)
    out: dict[str, Any] = {"session_id": sid, **result}
    return JSONResponse(out)


if _HTTP_MOUNT:
    app = FastAPI(title="Know Me", version=__version__)

    @app.get("/", include_in_schema=False)
    async def _redirect_to_mount() -> RedirectResponse:
        return RedirectResponse(url=f"{_HTTP_MOUNT}/", status_code=307)

    app.mount(_HTTP_MOUNT, inner)
else:
    app = inner
