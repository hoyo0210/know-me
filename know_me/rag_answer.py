"""
E02 — KM-202：基于检索片段的生成（证据约束）。

编排顺序（建议对照阅读 `retrieval` → `llm`）：
1. `retrieve`：拿到 Top-K `RetrievedChunk`。
2. 若为空：**不调用** LLM，直接返回固定拒答文案（避免模型用预训练知识「瞎补」）。
3. `retrieved_to_citation_block`：把片段变成带编号与 source 的纯文本证据块。
4. `chat_complete`：system = `RAG_SYSTEM_PROMPT`，user = 问题 + 证据块；temperature 来自配置。
5. 组装 `RAGAnswer`：`retrieved` 保留全量命中便于审计；`citations` 提供精简引用表。

后续 E03（HTTP `/chat`）可原样调用 `answer_with_rag(settings, query)`，仅多一层会话与流式包装。
"""

from __future__ import annotations

import logging

from know_me.llm import chat_complete
from know_me.prompts import RAG_SYSTEM_PROMPT
from know_me.retrieval import retrieve, retrieved_to_citation_block
from know_me.settings import IndexSettings
from know_me.types_rag import RAGAnswer

log = logging.getLogger(__name__)


def answer_with_rag(
    settings: IndexSettings,
    query: str,
    *,
    top_k: int | None = None,
) -> RAGAnswer:
    """
    对 `query` 检索后调用 LLM 生成回答。

    若未命中任何片段：不调用 LLM，返回简短拒答说明（符合「无证据不编造」）。
    """
    chunks = retrieve(settings, query, top_k=top_k)
    retrieved_dicts = [c.to_dict() for c in chunks]

    if not chunks:
        log.warning("检索零命中，跳过 LLM")
        return RAGAnswer(
            answer_text="资料库中未检索到与问题相关的片段；请先补充语料并执行 `know-me-index build-index`，或换一种提问方式。",
            retrieved=[],
            citations=[],
        )

    evidence = retrieved_to_citation_block(chunks)
    user_content = (
        f"用户问题：\n{query.strip()}\n\n"
        f"依据片段（仅可引用以下内容作答）：\n{evidence}\n\n"
        "请作答，并在末尾按系统要求列出引用。"
    )
    messages = [
        {"role": "system", "content": RAG_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    text = chat_complete(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
        model=settings.openai_chat_model,
        messages=messages,
        temperature=settings.llm_temperature,
    )

    citations: list[dict] = []
    for idx, ch in enumerate(chunks, start=1):
        citations.append(
            {
                "ref": idx,
                "chunk_id": ch.chunk_id,
                "source": ch.metadata.get("source", ""),
                "date": ch.metadata.get("date", ""),
                "distance": ch.distance,
            },
        )
    log.info("RAG 完成，引用条数=%s", len(citations))
    return RAGAnswer(answer_text=text.strip(), retrieved=retrieved_dicts, citations=citations)
