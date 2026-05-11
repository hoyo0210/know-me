"""E02 RAG 用到的数据结构（检索命中与最终回答）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RetrievedChunk:
    """单次向量检索命中的一条语料片段。"""

    chunk_id: str
    text: str
    distance: float | None
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "distance": self.distance,
            "metadata": dict(self.metadata),
        }


@dataclass
class RAGAnswer:
    """检索 + LLM 后的可交付结果（便于 API 或 CLI 输出）。"""

    answer_text: str
    retrieved: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
