"""
E01 用到的核心数据结构（与「磁盘文件 → 向量库中的一条记录」一一对应）。

阅读顺序建议：
1. RawDocument：加载器读完一个 .md 后得到什么
2. TextChunk：切分器把长文拆成多块后，每一块长什么样（即将写入 Chroma 的一行逻辑数据）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 文档与示例仓库常用的语料一级目录名；建索引时**不再**仅限这些，而是自动扫描 corpus_root 下全部一级子目录
CORPUS_KINDS: tuple[str, ...] = ("about_me", "faq", "hr_faq", "hr_screening")


def is_corpus_kind(name: str) -> bool:
    """判断是否为内置命名的语料子目录（与 CORPUS_KINDS 对齐；任意子目录名均可用于建索引）。"""
    return name in CORPUS_KINDS


@dataclass(frozen=True)
class RawDocument:
    """磁盘上的一份语料（切分前）。

    - corpus_kind：来自父目录名，用于区分业务场景（自我介绍 / FAQ / HR 等）
    - source：相对 corpus 根的路径，作为「出处」写入 metadata（例如 about_me/profile.md）
    - path：绝对路径，仅管道内部使用（取 mtime、调试）
    - body：去掉 YAML 头后的 Markdown 正文，供切分器使用
    - front_matter：YAML 头解析成的字典，用于 date / topic / audience 等
    """

    corpus_kind: str
    source: str
    path: Path
    body: str
    front_matter: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TextChunk:
    """一条可写入向量库的文本块（切分后）。

    chunk_id：在 Chroma 里作为主键；稳定 id 便于 upsert 覆盖同一条内容。
    text：该块实际参与「嵌入」和「检索时展示」的字符串。
    metadata：随向量一起存/filter 的键值（PRD 里的 source、date 等）。
    """

    chunk_id: str
    text: str
    metadata: dict[str, Any]
