from __future__ import annotations

import logging
from typing import Any

from know_me.chroma_store import add_chunks, get_client, get_or_create_collection, reset_collection
from know_me.embeddings import get_embedder
from know_me.loaders import iter_markdown_documents
from know_me.settings import IndexSettings
from know_me.splitting import split_document

log = logging.getLogger(__name__)


def build_index(settings: IndexSettings, *, reset: bool) -> dict[str, Any]:
    """
    E01 主流程：加载 → 切分 → 嵌入（经 Chroma 回调）→ 持久化。

    返回简单统计信息，便于日志与自动化检查。
    """
    client = get_client(settings.chroma_path)
    if reset:
        reset_collection(client, settings.collection_name)

    embedder = get_embedder(
        settings.embed_backend,
        ollama_base_url=settings.ollama_base_url,
        ollama_model=settings.ollama_embed_model,
        fake_dim=settings.fake_embedding_dim,
    )
    collection = get_or_create_collection(client, settings.collection_name, embedder)

    doc_count = 0
    chunk_count = 0
    ids: list[str] = []
    docs: list[str] = []
    metas: list[dict[str, Any]] = []

    for raw in iter_markdown_documents(settings.corpus_root):
        doc_count += 1
        for ch in split_document(
            raw,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        ):
            chunk_count += 1
            ids.append(ch.chunk_id)
            docs.append(ch.text)
            # Chroma metadata：值需为 str / int / float / bool；audience 空串可接受
            meta = {k: v for k, v in ch.metadata.items() if v is not None}
            metas.append(meta)

    if not ids:
        log.warning("未生成任何 chunk：请检查 %s 下语料是否为空", settings.corpus_root)
    else:
        add_chunks(collection, ids=ids, documents=docs, metadatas=metas)

    return {
        "documents": doc_count,
        "chunks": chunk_count,
        "collection": settings.collection_name,
        "chroma_path": str(settings.chroma_path),
    }
