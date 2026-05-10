from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class IndexSettings:
    """可通过环境变量覆盖，便于运维与 CI。"""

    corpus_root: Path
    chroma_path: Path
    chunk_size: int
    chunk_overlap: int
    collection_name: str
    ollama_base_url: str
    ollama_embed_model: str
    embed_backend: str
    fake_embedding_dim: int

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
            ollama_base_url=os.environ.get("KNOW_ME_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
            ollama_embed_model=os.environ.get(
                "KNOW_ME_OLLAMA_EMBED_MODEL",
                "qwen3-embedding:4b",
            ),
            # 默认 fake：无 Ollama 也能跑通管道；生产请改为 ollama 并配置 Qwen 嵌入
            embed_backend=os.environ.get("KNOW_ME_EMBED_BACKEND", "fake").lower(),
            fake_embedding_dim=_env_int("KNOW_ME_FAKE_EMBED_DIM", 768),
        )
