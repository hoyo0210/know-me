"""
Chroma 持久化封装：把「向量 + 原文 + metadata」写到本地目录。

几个容易混淆的概念：
- PersistentClient(path=...)：在磁盘 path 下维护数据库文件；重启进程后索引仍在。
- Collection（集合）：类似「一张表」，里面有 ids、documents、embeddings、metadatas。
- embedding_function：告诉 Chroma「如何把 document 字符串变成向量」。
  本项目中我们用 ChromaKnowMeEmbedding 包一层，内部再调用你的 OllamaEmbedder / FakeEmbedder。

写入为何用 upsert 而非 add：
- upsert：id 已存在则覆盖，不存在则插入；适合「重复构建同一语料」而不想先手动删库。
- 若切换嵌入模型导致向量维度变化，Chroma 会报错：此时应 CLI --reset 删集合重建。

批处理 batch_size：
- 单次 add/upsert 行数过多会占内存；分批写入更稳（尤其大语料）。
"""

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
    """
    Chroma 要求的嵌入函数签名：__call__(self, input: Documents) -> Embeddings。

    Chroma 在 upsert/add 时会传入一批文档字符串，由该函数返回对应向量；
    我们直接把调用转给业务侧的 Embedder.embed，保持「业务逻辑」与「存储 SDK」解耦。
    """

    def __init__(self, embedder: "Embedder") -> None:
        self._embedder = embedder

    def __call__(self, input: Documents) -> Embeddings:
        return self._embedder.embed(list(input))


def get_client(chroma_path: Path) -> chromadb.PersistentClient:
    """创建/连接本地持久化客户端；目录不存在则自动创建。"""
    chroma_path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(chroma_path))


def reset_collection(client: chromadb.PersistentClient, name: str) -> None:
    """删除整个集合（若不存在则忽略）。用于「全量重建」前清表。"""
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
    """获取或创建带自定义嵌入函数的集合。"""
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
    """分批 upsert，三列表长度必须一致（第 i 条共用 ids[i]、documents[i]、metadatas[i]）。"""
    for i in range(0, len(ids), batch_size):
        collection.upsert(
            ids=ids[i : i + batch_size],
            documents=documents[i : i + batch_size],
            metadatas=metadatas[i : i + batch_size],
        )
