"""
索引构建相关的「运行时配置」。

嵌入模型：
- 仅支持 **OpenAI 兼容** 的 `POST /v1/embeddings`（LM Studio、llama-server 兼容模式、多数云端网关）。
- 须配置 `KNOW_ME_OPENAI_EMBED_MODEL`；`KNOW_ME_OPENAI_BASE_URL` 默认指向本机 LM Studio 常见端口。

各环境变量在 IndexSettings 字段旁用注释标出，便于对照 `.env` 或 systemd 配置。
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
    # KNOW_ME_OPENAI_BASE_URL：须含 /v1 前缀，例如 http://127.0.0.1:1234/v1
    openai_base_url: str
    # KNOW_ME_OPENAI_API_KEY：本地 LM Studio 常为空；云端兼容网关按需填写
    openai_api_key: str
    # KNOW_ME_OPENAI_EMBED_MODEL：在服务端界面或 API 中显示的模型 id（必填）
    openai_embed_model: str
    # KNOW_ME_OPENAI_EMBED_BATCH_SIZE：单请求最多多少条文本（过大可能被服务端拒绝）
    openai_embed_batch_size: int

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
            openai_base_url=os.environ.get("KNOW_ME_OPENAI_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/"),
            openai_api_key=os.environ.get("KNOW_ME_OPENAI_API_KEY", ""),
            openai_embed_model=os.environ.get("KNOW_ME_OPENAI_EMBED_MODEL", "").strip(),
            openai_embed_batch_size=_env_int("KNOW_ME_OPENAI_EMBED_BATCH_SIZE", 32),
        )
