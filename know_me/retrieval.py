"""
E02 — KM-201：向量检索 Top-K。

核心思路：
1. **同一嵌入空间**：检索时必须使用与建索引**相同的** `OpenAICompatibleEmbedder`
   （同一 `base_url` + `embed_model`）。Chroma 通过集合上绑定的 `embedding_function`，
   对 `query_texts` 自动做「问句 → 向量 → 近邻搜索」。
2. **Top-K**：返回最相近的 K 条 chunk；K 越大上下文越全，但噪声越多、占用上下文越长。
   默认 `settings.rag_top_k`，CLI 可用 `--top-k` 覆盖。

Chroma `query` 返回结构说明：
- 外层列表维度为「查询条数」；本模块每次只查一条问句，故取 `[0]`。
- `distances`：与 `ids` / `documents` / `metadatas` 下标对齐；具体是距离还是相似度由集合配置决定，
  一般可理解为「越小越近」（以你本地 Chroma 版本文档为准）。
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

    # 与 pipeline.build_index 一致：同一 chroma 路径 + 集合名 + 嵌入实现
    client = get_client(settings.chroma_path)
    embedder = build_embedder(settings)
    collection = get_or_create_collection(client, settings.collection_name, embedder)

    # query_texts：Chroma 内部调用 embedding_function 将问句转向量，再检索
    raw = collection.query(
        query_texts=[q],
        n_results=k,
        include=["documents", "metadatas", "distances"],
    )
    # 以下四个字段均为「按查询批次」的列表；本处仅一批故取第一行
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
    """
    将命中片段拼成一段「证据上下文」，注入 LLM 的 user 消息中。

    编号 [1]、[2]… 与 `rag_answer` 里 `citations[].ref` 一致，便于模型在文末引用时对齐。
    """
    lines: list[str] = []
    for idx, ch in enumerate(chunks, start=1):
        src = ch.metadata.get("source", "")
        date = ch.metadata.get("date", "")
        topic = ch.metadata.get("topic", "")
        lines.append(
            f"[{idx}] (source={src!r}, date={date!r}, topic={topic!r})\n{ch.text.strip()}",
        )
    return "\n\n".join(lines)
