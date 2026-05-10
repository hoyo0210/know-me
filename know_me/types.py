from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

CorpusKind = Literal["about_me", "faq", "hr_faq", "hr_screening"]

CORPUS_KINDS: tuple[CorpusKind, ...] = ("about_me", "faq", "hr_faq", "hr_screening")


def is_corpus_kind(name: str) -> bool:
    return name in CORPUS_KINDS


@dataclass(frozen=True)
class RawDocument:
    """磁盘上的一份语料（切分前）。"""

    corpus_kind: CorpusKind
    source: str
    path: Path
    body: str
    front_matter: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TextChunk:
    """一条可写入向量库的文本块。"""

    chunk_id: str
    text: str
    metadata: dict[str, Any]
