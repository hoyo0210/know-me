"""
切分与元数据合并：把 RawDocument（整篇）拆成多条 TextChunk（短段）。

为何必须切分：
- 嵌入模型与向量检索都有长度/粒度限制；太长整篇嵌入会「语义模糊」，太短则上下文不足。
- PRD 默认 chunk_size≈512、overlap≈50：在「可检索粒度」与「单块信息量」之间折中。
  当前 length_function=len 表示按「字符数」计量（中文场景常用；日后可换成按 token 计）。

chunk_id 设计：
- 用 source + 块序号 + 文本前缀做哈希，使「同一文档同一位置」重建索引时 id 尽量稳定，便于 upsert。
- 若大幅改写段落，前缀变化会导致新 id（旧向量可能残留）；全量一致性请用 CLI --reset。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from langchain_text_splitters import RecursiveCharacterTextSplitter

from know_me.types import RawDocument, TextChunk

log = logging.getLogger(__name__)


def _file_mtime_date(path: Path) -> str | None:
    """取文件修改时间对应的 UTC 日期字符串（YYYY-MM-DD），读失败则 None。"""
    try:
        ts = path.stat().st_mtime
        return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
    except OSError:
        return None


def build_chunk_metadata(doc: RawDocument) -> dict[str, Any]:
    """
    生成「同一条 RawDocument 拆出的所有 TextChunk」共享的 metadata。

    优先级（与 PRD 对齐）：
    - date：YAML 的 date > 文件 mtime 日期 > 空串
    - topic：YAML 的 topic > 用 corpus_kind 兜底
    - audience：YAML 可省略；省略时存空串（Chroma 不接受 None 作 metadata 值）
    - privacy_level：默认 public
    """
    fm = doc.front_matter
    mtime = _file_mtime_date(doc.path)
    date = str(fm.get("date") or mtime or "")
    topic = str(fm.get("topic") or doc.corpus_kind)
    audience = fm.get("audience")
    audience_s = str(audience) if audience is not None else ""
    privacy = str(fm.get("privacy_level") or "public")
    return {
        "source": doc.source,
        "date": date,
        "topic": topic,
        "audience": audience_s,
        "privacy_level": privacy,
        "corpus_kind": doc.corpus_kind,
    }


def split_document(
    doc: RawDocument,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> Iterator[TextChunk]:
    """
    将单篇文档切分为多条 TextChunk。

    RecursiveCharacterTextSplitter：尽量在换行、句号等「自然边界」断开，
    避免把单词或 Markdown 标题粗暴截断（比固定宽度硬切更友好）。
    """
    if not doc.body.strip():
        log.warning("跳过空文档：%s", doc.source)
        return
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
    )
    base_meta = build_chunk_metadata(doc)
    pieces = splitter.split_text(doc.body)
    for idx, text in enumerate(pieces):
        # 取 text 前 64 字参与哈希：同一 idx 若正文变了，会得到新 id，避免静默覆盖错误向量
        digest = hashlib.sha256(f"{doc.source}:{idx}:{text[:64]}".encode("utf-8")).hexdigest()
        chunk_id = f"{digest[:24]}"
        yield TextChunk(chunk_id=chunk_id, text=text, metadata={**base_meta, "chunk_index": idx})
