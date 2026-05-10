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
    try:
        ts = path.stat().st_mtime
        return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
    except OSError:
        return None


def build_chunk_metadata(
    doc: RawDocument,
) -> dict[str, Any]:
    """每条 chunk 共享的元数据（PRD：source / date / topic / audience / privacy_level）。"""
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
        digest = hashlib.sha256(f"{doc.source}:{idx}:{text[:64]}".encode("utf-8")).hexdigest()
        chunk_id = f"{digest[:24]}"
        yield TextChunk(chunk_id=chunk_id, text=text, metadata={**base_meta, "chunk_index": idx})
