"""
E01 编排层：把 loader、splitter、embedder、chroma 串成一次完整的「建索引」事务。

数据流（建议对照阅读）：
1. iter_markdown_documents：扫描 corpus，产出多篇 RawDocument
2. split_document：每篇变成多条 TextChunk（带 metadata）
3. get_or_create_collection：准备好 Chroma 集合（绑定嵌入函数）
4. add_chunks：Chroma 内部会对每条 document 调嵌入函数，写入向量 + metadata

注意：当前实现先把「所有 chunk」收集到内存再一次性 upsert；
语料极大时可改为边遍历边分批写入，降低峰值内存（后续优化项）。

与 E02 的关系：本管道写入的 Chroma 集合与嵌入模型，由 `retrieval.retrieve` 以相同路径与
`build_embedder(settings)` 再次打开；改嵌入模型后请对索引 `--reset` 重建。
"""

from __future__ import annotations

import logging
from typing import Any

from know_me.index.chroma_store import add_chunks, get_client, get_or_create_collection, reset_collection
from know_me.index.embeddings import build_embedder
from know_me.index.loaders import iter_markdown_documents
from know_me.index.splitting import split_document
from know_me.core.settings import IndexSettings

log = logging.getLogger(__name__)


def build_index(settings: IndexSettings, *, reset: bool) -> dict[str, Any]:
    """
    构建（或增量 upsert）向量索引。

    参数 reset：
    - True：先删集合再建，保证与「当前磁盘语料」完全一致，无残留旧 chunk
    - False：保留集合，仅按 chunk_id upsert；适合频繁小改，但要注意 id 策略与嵌入维度变更

    返回值：简单统计 dict，便于 shell 脚本判断成功与否或打日志。
    """
    # 1) 连接本地 Chroma
    client = get_client(settings.chroma_path)
    if reset:
        reset_collection(client, settings.collection_name)

    # 2) OpenAI 兼容嵌入（LM Studio / llama-server 等），并创建带该嵌入函数的集合
    embedder = build_embedder(settings)
    collection = get_or_create_collection(client, settings.collection_name, embedder)

    doc_count = 0
    chunk_count = 0
    ids: list[str] = []
    docs: list[str] = []
    metas: list[dict[str, Any]] = []

    # 3) 全量遍历：文档级计数 + 展开为 chunk 行
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
            # Chroma 要求 metadata 值为 str / int / float / bool；不能塞 None
            meta = {k: v for k, v in ch.metadata.items() if v is not None}
            metas.append(meta)

    # 4) 写入向量库（内部会触发嵌入）
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
