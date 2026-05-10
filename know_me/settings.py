"""
索引构建相关的「运行时配置」。

设计意图：
- 默认值保证「克隆仓库 → 安装依赖 → 一条命令」能跑通（默认嵌入用 fake，不依赖 Ollama）
- 部署到本机 / 服务器时，用环境变量改路径与模型名，无需改代码

各环境变量在 IndexSettings 字段旁用注释标出，便于对照 .env 或 systemd 配置。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    """从环境变量读整数；未设置或空串则用 default。"""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class IndexSettings:
    """一次 `build_index` 所需的全部参数（由 CLI 与环境变量合并得到）。"""

    # KNOW_ME_CORPUS_ROOT（CLI --corpus-root 优先）
    corpus_root: Path
    # KNOW_ME_CHROMA_PATH（CLI --chroma-path 优先）
    chroma_path: Path
    # KNOW_ME_CHUNK_SIZE / KNOW_ME_CHUNK_OVERLAP：切分窗口；PRD 建议约 512 / 50
    chunk_size: int
    chunk_overlap: int
    # KNOW_ME_CHROMA_COLLECTION：同一机器多项目时可改成不同集合名
    collection_name: str
    # KNOW_ME_OLLAMA_URL：Ollama 的 HTTP 根地址（嵌入与将来对话模型可能共用）
    ollama_base_url: str
    # KNOW_ME_OLLAMA_EMBED_MODEL：须与 `ollama list` 中名称一致（你本地拉好的 Qwen 嵌入）
    ollama_embed_model: str
    # KNOW_ME_EMBED_BACKEND：ollama=真实向量；fake=仅占位，无语义检索
    embed_backend: str
    # KNOW_ME_FAKE_EMBED_DIM：fake 后端向量维度；若切到 ollama 且维度不同，需 --reset 重建集合
    fake_embedding_dim: int

    @staticmethod
    def from_env(corpus_root: Path | None = None, chroma_path: Path | None = None) -> "IndexSettings":
        """组装配置：显式传入的路径（来自 Typer CLI）优先于环境变量。"""
        root = corpus_root or Path(os.environ.get("KNOW_ME_CORPUS_ROOT", "corpus")).resolve()
        chroma = chroma_path or Path(os.environ.get("KNOW_ME_CHROMA_PATH", "data/chroma")).resolve()
        return IndexSettings(
            corpus_root=root,
            chroma_path=chroma,
            chunk_size=_env_int("KNOW_ME_CHUNK_SIZE", 512),
            chunk_overlap=_env_int("KNOW_ME_CHUNK_OVERLAP", 50),
            collection_name=os.environ.get("KNOW_ME_CHROMA_COLLECTION", "know_me_corpus"),
            ollama_base_url=os.environ.get("KNOW_ME_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
            ollama_embed_model=os.environ.get(
                "KNOW_ME_OLLAMA_EMBED_MODEL",
                "qwen3-embedding:4b",
            ),
            # 默认 fake：CI / 新手环境无需起 Ollama；上线前改为 ollama 并配置真实嵌入模型
            embed_backend=os.environ.get("KNOW_ME_EMBED_BACKEND", "fake").lower(),
            fake_embedding_dim=_env_int("KNOW_ME_FAKE_EMBED_DIM", 768),
        )
