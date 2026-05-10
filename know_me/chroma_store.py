from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import chromadb
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings

if TYPE_CHECKING:
    from know_me.embeddings import Embedder

log = logging.getLogger(__name__)


class ChromaKnowMeEmbedding(EmbeddingFunction[Documents]):
    """把业务侧的 Embedder 适配为 Chroma 的 EmbeddingFunction。"""

    def __init__(self, embedder: "Embedder") -> None:
        self._embedder = embedder

    def __call__(self, input: Documents) -> Embeddings:
        return self._embedder.embed(list(input))


def get_client(chroma_path: Path) -> chromadb.PersistentClient:
    chroma_path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(chroma_path))


def reset_collection(client: chromadb.PersistentClient, name: str) -> None:
    try:
        client.delete_collection(name)
        log.info("已删除集合：%s", name)
    except Exception:
        log.debug("删除集合时忽略（可能不存在）：%s", name)


def get_or_create_collection(
    client: chromadb.PersistentClient,
    name: str,
    embedder: "Embedder",
):
    ef = ChromaKnowMeEmbedding(embedder)
    return client.get_or_create_collection(name=name, embedding_function=ef)


def add_chunks(
    collection,
    *,
    ids: list[str],
    documents: list[str],
    metadatas: list[dict[str, Any]],
    batch_size: int = 64,
) -> None:
    for i in range(0, len(ids), batch_size):
        collection.upsert(
            ids=ids[i : i + batch_size],
            documents=documents[i : i + batch_size],
            metadatas=metadatas[i : i + batch_size],
        )
