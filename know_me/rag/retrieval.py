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
import re
from typing import Any

from know_me.index.chroma_store import get_client, get_or_create_collection
from know_me.index.embeddings import build_embedder
from know_me.core.settings import IndexSettings
from know_me.core.types_rag import RetrievedChunk

log = logging.getLogger(__name__)

# E04 KM-403：多取若干条后把 hr_* 语料前置，再截断回 top_k（轻量重排，非训练级意图模型）
_HR_FETCH_CAP = 60


def is_hr_intent(query: str) -> bool:
    """
    轻量 HR 意图识别：关键词 + 常见英文缩写。

    误报可接受（仅影响检索加权）；漏报时仍走常规模型检索。
    """
    s = (query or "").strip()
    if not s:
        return False
    sl = s.lower()
    if re.search(r"\bhr\b", sl) or re.search(r"\bhuman resources\b", sl):
        return True
    needles_cn = (
        "人事",
        "初筛",
        "猎头",
        "薪酬",
        "薪资",
        "工资",
        "月薪",
        "年薪",
        "总包",
        "期权",
        "股权",
        "加班",
        "五险一金",
        "背调",
        "背景调查",
        "到岗",
        "入职",
        "远程",
        "混合办公",
        "签证",
        "工签",
        "福利待遇",
    )
    if any(n in s for n in needles_cn):
        return True
    return bool(re.search(r"\b(salary|compensation|relocation|benefits|on-?site)\b", sl))


def _chunk_is_hr_audience(meta: dict[str, Any]) -> bool:
    """命中条是否来自 HR 初筛语料（用于 KM-403 重排）。"""
    aud = str(meta.get("audience", "")).lower()
    if "hr" in aud:
        return True
    ck = str(meta.get("corpus_kind", ""))
    return ck in ("hr_faq", "hr_screening")


def retrieve(
    settings: IndexSettings,
    query: str,
    *,
    top_k: int | None = None,
    use_hr_boost: bool | None = None,
) -> list[RetrievedChunk]:
    """
    对 `query` 做相似度检索，返回最多 `top_k` 条命中（含 metadata 与距离）。

    `top_k` 未传时使用 `settings.rag_top_k`。
    `use_hr_boost` 为 None 时遵循 `settings.rag_hr_boost`；为 False 时本调用不做 HR 加权大批量拉取。
    """
    k = top_k if top_k is not None else settings.rag_top_k
    k = max(1, k)
    q = query.strip()
    if not q:
        return []

    rag_ok = settings.rag_hr_boost if use_hr_boost is None else use_hr_boost
    use_boost = rag_ok and is_hr_intent(q)
    n_fetch = min(_HR_FETCH_CAP, max(k * 3, k)) if use_boost else k

    # 与 pipeline.build_index 一致：同一 chroma 路径 + 集合名 + 嵌入实现
    client = get_client(settings.chroma_path)
    embedder = build_embedder(settings)
    collection = get_or_create_collection(client, settings.collection_name, embedder)

    # query_texts：Chroma 内部调用 embedding_function 将问句转向量，再检索
    raw = collection.query(
        query_texts=[q],
        n_results=n_fetch,
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

    if use_boost and len(out) > k:
        hr_chunks = [c for c in out if _chunk_is_hr_audience(c.metadata)]
        other_chunks = [c for c in out if not _chunk_is_hr_audience(c.metadata)]

        def _dist_key(c: RetrievedChunk) -> tuple[bool, float]:
            return (c.distance is None, c.distance if c.distance is not None else 0.0)

        hr_chunks.sort(key=_dist_key)
        other_chunks.sort(key=_dist_key)
        out = (hr_chunks + other_chunks)[:k]
        log.info("检索 HR 加权：n_fetch=%s，重排后取 top_k=%s（hr 条数=%s）", n_fetch, k, len(hr_chunks))
    else:
        out = out[:k]
        log.info("检索命中 %s 条（top_k=%s，n_fetch=%s）", len(out), k, n_fetch)
    return out


def citation_dicts_from_chunks(chunks: list[RetrievedChunk]) -> list[dict[str, Any]]:
    """将命中列表转为与 `RAGAnswer.citations` 一致的精简 dict（E02/E03 共用）。"""
    citations: list[dict[str, Any]] = []
    for idx, ch in enumerate(chunks, start=1):
        citations.append(
            {
                "ref": idx,
                "chunk_id": ch.chunk_id,
                "source": ch.metadata.get("source", ""),
                "date": ch.metadata.get("date", ""),
                "audience": ch.metadata.get("audience", ""),
                "corpus_kind": ch.metadata.get("corpus_kind", ""),
                "distance": ch.distance,
            },
        )
    return citations


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
        aud = ch.metadata.get("audience", "")
        ck = ch.metadata.get("corpus_kind", "")
        lines.append(
            f"[{idx}] (source={src!r}, date={date!r}, topic={topic!r}, audience={aud!r}, corpus_kind={ck!r})\n"
            f"{ch.text.strip()}",
        )
    return "\n\n".join(lines)
