"""
E02 — KM-202：基于检索片段的生成（证据约束）。

编排顺序（建议对照阅读 `retrieval` → `llm`）：
0. `job_intent.should_retrieve_personal_corpus`：非求职向则**不检索**；纯寒暄再走 `greeting_fast_answer` **不调 LLM**；其余走 `NON_JOB_QUERY_SYSTEM_PROMPT` 一轮对话。
1. `retrieve`：拿到 Top-K `RetrievedChunk`。
2. 若为空：**不调用** LLM，直接返回固定拒答文案（避免模型用预训练知识「瞎补」）。
3. `retrieved_to_citation_block`：把片段变成带编号与 source 的纯文本证据块。
4. **非流式**：`chat_complete` 一次取回全文。
5. **流式（CLI 默认）**：`iter_chat_complete` 增量产出正文；`RAGStreamSession` 在迭代结束后汇总 `full_text`。
6. 组装 `RAGAnswer`：`retrieved` / `citations` 在检索后即确定，与是否流式无关。

后续 E03（HTTP `/chat` + SSE）可复用 `iter_chat_complete` 与 `RAGStreamSession.iter_assistant_text()`。
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Iterator

from know_me.rag.job_intent import (
    greeting_fast_answer,
    is_greeting_only_message,
    should_retrieve_personal_corpus,
)
from know_me.rag.llm import chat_complete, iter_chat_complete
from know_me.rag.prompts import NON_JOB_QUERY_SYSTEM_PROMPT, RAG_SYSTEM_PROMPT
from know_me.rag.retrieval import citation_dicts_from_chunks, retrieve, retrieved_to_citation_block
from know_me.core.settings import IndexSettings
from know_me.observability.trace_log import emit_structured_trace
from know_me.core.types_rag import RAGAnswer

if TYPE_CHECKING:
    from know_me.core.types_rag import RetrievedChunk

log = logging.getLogger(__name__)

_ZERO_HIT = (
    "资料库中未检索到与问题相关的片段；请先补充语料并执行 `know-me build-index`，或换一种提问方式。"
)


def _build_rag_messages(query: str, chunks: list["RetrievedChunk"]) -> list[dict[str, str]]:
    evidence = retrieved_to_citation_block(chunks)
    user_content = (
        f"用户问题：\n{query.strip()}\n\n"
        f"依据片段（仅可引用以下内容作答）：\n{evidence}\n\n"
        "请按系统提示：寒暄类可短答；事实类只用依据片段，以完整书面句表达，勿为格式拆句。**换行会被前端拆成多条气泡**，连贯内容尽量写在同一行；仅在确有第二条独立要点时才使用一次换行。无依据则按系统提示的兜底句，勿编造。"
    )
    return [
        {"role": "system", "content": RAG_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _build_non_job_messages(query: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": NON_JOB_QUERY_SYSTEM_PROMPT},
        {"role": "user", "content": f"对方说：\n{query.strip()}"},
    ]


def answer_with_rag(
    settings: IndexSettings,
    query: str,
    *,
    top_k: int | None = None,
) -> RAGAnswer:
    """
    非流式 RAG：一次性返回 `RAGAnswer`（供 `--no-stream`、`--json` 或程序化聚合）。
    """
    t0 = time.perf_counter()
    if not should_retrieve_personal_corpus(query):
        qs = query.strip()
        if is_greeting_only_message(qs):
            text = greeting_fast_answer(qs)
            t1 = time.perf_counter()
            log.info("纯寒暄：本地即时回复（非流式 query）")
            emit_structured_trace(
                settings,
                {
                    "event": "rag_answer",
                    "path": "greeting_local",
                    "user_query": qs[:2000],
                    "chunk_ids": [],
                    "embed_model": settings.openai_embed_model,
                    "chat_model": settings.openai_chat_model,
                    "latency_retrieve_ms": 0.0,
                    "latency_llm_ms": 0.0,
                    "latency_total_ms": round((t1 - t0) * 1000.0, 2),
                    "usage": None,
                    "top_k": top_k if top_k is not None else settings.rag_top_k,
                },
            )
            return RAGAnswer(answer_text=text, retrieved=[], citations=[])
        log.info("意图判定：非求职相关，跳过检索")
        cc = chat_complete(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            model=settings.openai_chat_model,
            messages=_build_non_job_messages(query),
            temperature=settings.llm_temperature,
        )
        t1 = time.perf_counter()
        emit_structured_trace(
            settings,
            {
                "event": "rag_answer",
                "path": "non_job_no_retrieve",
                "user_query": query.strip()[:2000],
                "chunk_ids": [],
                "embed_model": settings.openai_embed_model,
                "chat_model": settings.openai_chat_model,
                "latency_retrieve_ms": 0.0,
                "latency_llm_ms": round((t1 - t0) * 1000.0, 2),
                "latency_total_ms": round((t1 - t0) * 1000.0, 2),
                "usage": cc.usage,
                "top_k": top_k if top_k is not None else settings.rag_top_k,
            },
        )
        return RAGAnswer(answer_text=cc.text.strip(), retrieved=[], citations=[])

    chunks = retrieve(settings, query, top_k=top_k)
    t1 = time.perf_counter()
    retrieved_dicts = [c.to_dict() for c in chunks]
    k_eff = top_k if top_k is not None else settings.rag_top_k

    if not chunks:
        log.warning("检索零命中，跳过 LLM")
        emit_structured_trace(
            settings,
            {
                "event": "rag_answer",
                "path": "non_stream",
                "user_query": query.strip()[:2000],
                "chunk_ids": [],
                "embed_model": settings.openai_embed_model,
                "chat_model": settings.openai_chat_model,
                "latency_retrieve_ms": round((t1 - t0) * 1000.0, 2),
                "latency_llm_ms": 0.0,
                "latency_total_ms": round((t1 - t0) * 1000.0, 2),
                "usage": None,
                "top_k": k_eff,
            },
        )
        return RAGAnswer(answer_text=_ZERO_HIT, retrieved=[], citations=[])

    messages = _build_rag_messages(query, chunks)
    cc = chat_complete(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
        model=settings.openai_chat_model,
        messages=messages,
        temperature=settings.llm_temperature,
    )
    t2 = time.perf_counter()
    citations = citation_dicts_from_chunks(chunks)
    log.info("RAG 完成（非流式），引用条数=%s", len(citations))
    emit_structured_trace(
        settings,
        {
            "event": "rag_answer",
            "path": "non_stream",
            "user_query": query.strip()[:2000],
            "chunk_ids": [c.chunk_id for c in chunks],
            "embed_model": settings.openai_embed_model,
            "chat_model": settings.openai_chat_model,
            "latency_retrieve_ms": round((t1 - t0) * 1000.0, 2),
            "latency_llm_ms": round((t2 - t1) * 1000.0, 2),
            "latency_total_ms": round((t2 - t0) * 1000.0, 2),
            "usage": cc.usage,
            "top_k": k_eff,
        },
    )
    return RAGAnswer(answer_text=cc.text.strip(), retrieved=retrieved_dicts, citations=citations)


class RAGStreamSession:
    """
    流式 RAG 会话：先完成检索并固定 citations / retrieved，再对 LLM 响应按增量迭代。

    用法（与 CLI 一致）::

        sess = RAGStreamSession(settings, question, top_k=5)
        for delta in sess.iter_assistant_text():
            sys.stdout.write(delta); sys.stdout.flush()
        # 结束后 sess.full_text 为完整拼接；sess.citations 已可用
    """

    def __init__(
        self,
        settings: IndexSettings,
        query: str,
        *,
        top_k: int | None = None,
    ) -> None:
        self._settings = settings
        self._query = query.strip()
        self._top_k_eff = top_k if top_k is not None else settings.rag_top_k
        self._skip_retrieve = not should_retrieve_personal_corpus(query)
        self._greeting_local: str | None = None
        if self._skip_retrieve and is_greeting_only_message(self._query):
            self._greeting_local = greeting_fast_answer(self._query)
        t_r0 = time.perf_counter()
        if self._skip_retrieve:
            log.info("意图判定：非求职相关，跳过检索（流式）")
            self.chunks = []
            self._retrieve_ms = (time.perf_counter() - t_r0) * 1000.0
        else:
            self.chunks = retrieve(settings, query, top_k=top_k)
            self._retrieve_ms = (time.perf_counter() - t_r0) * 1000.0
        self.retrieved: list[dict] = [c.to_dict() for c in self.chunks]
        self.citations: list[dict] = citation_dicts_from_chunks(self.chunks) if self.chunks else []
        self.full_text: str = ""

    def iter_assistant_text(self) -> Iterator[str]:
        """产出 assistant 文本增量；无命中时只产出一条固定说明。"""
        if self._greeting_local is not None:
            self.full_text = self._greeting_local
            emit_structured_trace(
                self._settings,
                {
                    "event": "rag_answer",
                    "path": "stream_greeting_local",
                    "user_query": self._query[:2000],
                    "chunk_ids": [],
                    "embed_model": self._settings.openai_embed_model,
                    "chat_model": self._settings.openai_chat_model,
                    "latency_retrieve_ms": round(self._retrieve_ms, 2),
                    "latency_llm_ms": 0.0,
                    "latency_total_ms": round(self._retrieve_ms, 2),
                    "usage": None,
                    "top_k": self._top_k_eff,
                },
            )
            yield self._greeting_local
            return
        if self._skip_retrieve:
            t_llm0 = time.perf_counter()
            buf: list[str] = []
            for piece in iter_chat_complete(
                base_url=self._settings.openai_base_url,
                api_key=self._settings.openai_api_key,
                model=self._settings.openai_chat_model,
                messages=_build_non_job_messages(self._query),
                temperature=self._settings.llm_temperature,
            ):
                buf.append(piece)
                yield piece
            self.full_text = "".join(buf).strip()
            llm_ms = (time.perf_counter() - t_llm0) * 1000.0
            emit_structured_trace(
                self._settings,
                {
                    "event": "rag_answer",
                    "path": "stream_non_job_no_retrieve",
                    "user_query": self._query[:2000],
                    "chunk_ids": [],
                    "embed_model": self._settings.openai_embed_model,
                    "chat_model": self._settings.openai_chat_model,
                    "latency_retrieve_ms": round(self._retrieve_ms, 2),
                    "latency_llm_ms": round(llm_ms, 2),
                    "latency_total_ms": round(self._retrieve_ms + llm_ms, 2),
                    "usage": None,
                    "top_k": self._top_k_eff,
                },
            )
            return
        if not self.chunks:
            log.warning("检索零命中，跳过 LLM（流式）")
            self.full_text = _ZERO_HIT
            emit_structured_trace(
                self._settings,
                {
                    "event": "rag_answer",
                    "path": "stream_zero_hit",
                    "user_query": self._query[:2000],
                    "chunk_ids": [],
                    "embed_model": self._settings.openai_embed_model,
                    "chat_model": self._settings.openai_chat_model,
                    "latency_retrieve_ms": round(self._retrieve_ms, 2),
                    "latency_llm_ms": 0.0,
                    "latency_total_ms": round(self._retrieve_ms, 2),
                    "usage": None,
                    "top_k": self._top_k_eff,
                },
            )
            yield _ZERO_HIT
            return
        messages = _build_rag_messages(self._query, self.chunks)
        buf: list[str] = []
        t_llm0 = time.perf_counter()
        for piece in iter_chat_complete(
            base_url=self._settings.openai_base_url,
            api_key=self._settings.openai_api_key,
            model=self._settings.openai_chat_model,
            messages=messages,
            temperature=self._settings.llm_temperature,
        ):
            buf.append(piece)
            yield piece
        self.full_text = "".join(buf).strip()
        llm_ms = (time.perf_counter() - t_llm0) * 1000.0
        log.info("RAG 完成（流式），引用条数=%s", len(self.citations))
        emit_structured_trace(
            self._settings,
            {
                "event": "rag_answer",
                "path": "stream",
                "user_query": self._query[:2000],
                "chunk_ids": [c.chunk_id for c in self.chunks],
                "embed_model": self._settings.openai_embed_model,
                "chat_model": self._settings.openai_chat_model,
                "latency_retrieve_ms": round(self._retrieve_ms, 2),
                "latency_llm_ms": round(llm_ms, 2),
                "latency_total_ms": round(self._retrieve_ms + llm_ms, 2),
                "usage": None,
                "top_k": self._top_k_eff,
            },
        )
