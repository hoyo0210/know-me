"""
E02 RAG 数据结构：从「向量检索的一条命中」到「可对外展示的完整回答」。

与 E01 的 `types.TextChunk` 的关系：
- `TextChunk` 描述的是**写入索引时**的一条切片；
- `RetrievedChunk` 描述的是**查询时** Chroma 返回的一条命中（含相似度距离）。
- 二者文本与 metadata 字段语义相近，但生命周期不同（入库 vs 出库）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RetrievedChunk:
    """
    单次向量检索命中的一条语料片段。

    - chunk_id：与 Chroma 中存储的 id 一致，便于与日志或后续排错对齐。
    - text：该片段正文（与建索引时的 document 一致）。
    - distance：Chroma 返回的距离/不相似度（具体定义与建库时空间度量有关）；越小通常越相似。
    - metadata：建索引时写入的 source / date / topic 等（PRD 可解释性）。
    """

    chunk_id: str
    text: str
    distance: float | None
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """便于 `--json` 输出或将来 REST API 序列化。"""
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "distance": self.distance,
            "metadata": dict(self.metadata),
        }


@dataclass
class RAGAnswer:
    """
    一次 RAG 调用的完整输出。

    - answer_text：面向用户的自然语言（由 LLM 生成，仍受系统提示约束）。
    - retrieved：原始命中列表的字典形式（调试 / 审计 / 前端「展开证据」）。
    - citations：精简引用表（ref 序号与 source/date 等，对应 KM-202 可追溯性）。
    """

    answer_text: str
    retrieved: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
