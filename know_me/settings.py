"""
Know Me 运行时配置（E01 建索引 + E02 检索与生成）。

嵌入与对话均走 **OpenAI 兼容** HTTP（同一 `KNOW_ME_OPENAI_BASE_URL` 下的 `/v1/embeddings` 与 `/v1/chat/completions`），
便于 LM Studio、llama-server 等本地或网关统一部署。

各环境变量在 `IndexSettings` 字段旁用注释标出，便于对照 `.env` 或 systemd 配置。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


@dataclass(frozen=True)
class IndexSettings:
    """建索引、检索、RAG 共用的配置（CLI 与环境变量合并）。"""

    # KNOW_ME_CORPUS_ROOT（仅 build-index 使用；CLI --corpus-root 优先）
    corpus_root: Path
    # KNOW_ME_CHROMA_PATH（CLI --chroma-path 优先）
    chroma_path: Path
    # KNOW_ME_CHUNK_SIZE / KNOW_ME_CHUNK_OVERLAP：切分窗口；PRD 建议约 512 / 50
    chunk_size: int
    chunk_overlap: int
    # KNOW_ME_CHROMA_COLLECTION
    collection_name: str
    # KNOW_ME_OPENAI_BASE_URL：须含 /v1，例如 http://127.0.0.1:1234/v1
    openai_base_url: str
    # KNOW_ME_OPENAI_API_KEY
    openai_api_key: str
    # KNOW_ME_OPENAI_EMBED_MODEL：嵌入模型 id（建索引与检索问句必须一致）
    openai_embed_model: str
    # KNOW_ME_OPENAI_EMBED_BATCH_SIZE
    openai_embed_batch_size: int
    # KNOW_ME_OPENAI_CHAT_MODEL：对话模型 id（仅 `query` / RAG 生成需要）
    openai_chat_model: str
    # KNOW_ME_RAG_TOP_K：默认检索条数
    rag_top_k: int
    # KNOW_ME_LLM_TEMPERATURE
    llm_temperature: float

    @staticmethod
    def from_env(corpus_root: Path | None = None, chroma_path: Path | None = None) -> "IndexSettings":
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
            openai_chat_model=os.environ.get("KNOW_ME_OPENAI_CHAT_MODEL", "").strip(),
            rag_top_k=_env_int("KNOW_ME_RAG_TOP_K", 5),
            llm_temperature=_env_float("KNOW_ME_LLM_TEMPERATURE", 0.2),
        )
