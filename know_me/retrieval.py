"""
E02 — KM-201：向量检索 Top-K。

使用与建索引时相同的嵌入函数打开集合，通过 Chroma 的 `query_texts` 将问句嵌入并在同一向量空间内检索。
`distance` 的含义取决于集合建索引时的空间度量（Chroma 默认常为 L2）；数值越小通常表示越近。
"""

from __future__ import annotations

import logging
from typing import Any

from know_me.chroma_store import get_client, get_or_create_collection
from know_me.embeddings import build_embedder
from know_me.settings import IndexSettings
from know_me.types_rag import RetrievedChunk

log = logging.getLogger(__name__)


def retrieve(
    settings: IndexSettings,
    query: str,
    *,
    top_k: int | None = None,
) -> list[RetrievedChunk]:
    """
    对 `query` 做相似度检索，返回最多 `top_k` 条命中（含 metadata 与距离）。

    `top_k` 未传时使用 `settings.rag_top_k`。
    """
    k = top_k if top_k is not None else settings.rag_top_k
    k = max(1, k)
    q = query.strip()
    if not q:
        return []

    client = get_client(settings.chroma_path)
    embedder = build_embedder(settings)
    collection = get_or_create_collection(client, settings.collection_name, embedder)

    raw = collection.query(
        query_texts=[q],
        n_results=k,
        include=["documents", "metadatas", "distances"],
    )
    ids_batch = raw.get("ids") or [[]]
    docs_batch = raw.get("documents") or [[]]
    meta_batch = raw.get("metadatas") or [[]]
    dist_batch = raw.get("distances") or [[]]

    ids = ids_batch[0] if ids_batch else []
    docs = docs_batch[0] if docs_batch else []
    metas = meta_batch[0] if meta_batch else []
    dists = dist_batch[0] if dist_batch else []

    out: list[RetrievedChunk] = []
    for i, chunk_id in enumerate(ids):
        text = docs[i] if i < len(docs) else ""
        meta: dict[str, Any] = dict(metas[i]) if i < len(metas) and metas[i] else {}
        dist: float | None = None
        if i < len(dists) and dists[i] is not None:
            try:
                dist = float(dists[i])
            except (TypeError, ValueError):
                dist = None
        out.append(RetrievedChunk(chunk_id=str(chunk_id), text=str(text), distance=dist, metadata=meta))
    log.info("检索命中 %s 条（top_k=%s）", len(out), k)
    return out


def retrieved_to_citation_block(chunks: list[RetrievedChunk]) -> str:
    """将命中片段格式化为注入 LLM 的「依据片段」文本块。"""
    lines: list[str] = []
    for idx, ch in enumerate(chunks, start=1):
        src = ch.metadata.get("source", "")
        date = ch.metadata.get("date", "")
        topic = ch.metadata.get("topic", "")
        lines.append(
            f"[{idx}] (source={src!r}, date={date!r}, topic={topic!r})\n{ch.text.strip()}",
        )
    return "\n\n".join(lines)
