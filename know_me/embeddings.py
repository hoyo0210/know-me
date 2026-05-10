from __future__ import annotations

import hashlib
import logging
import math
from typing import Protocol, Sequence, runtime_checkable

import httpx

log = logging.getLogger(__name__)


@runtime_checkable
class Embedder(Protocol):
    """嵌入后端：把多条文本映射为同维向量。"""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        ...


class OllamaEmbedder:
    """通过 Ollama HTTP API 调用本地嵌入模型（对齐 PRD：Qwen3 Embedding GGUF 可先注册为同名 Modelfile）。"""

    def __init__(self, base_url: str, model: str) -> None:
        self._base = base_url.rstrip("/")
        self._model = model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        with httpx.Client(timeout=120.0) as client:
            for t in texts:
                r = client.post(
                    f"{self._base}/api/embeddings",
                    json={"model": self._model, "prompt": t},
                )
                r.raise_for_status()
                data = r.json()
                vec = data.get("embedding")
                if not isinstance(vec, list) or not vec:
                    raise RuntimeError(f"Ollama 返回异常：{data!r}")
                out.append([float(x) for x in vec])
        return out


class FakeEmbedder:
    """无外部依赖的伪向量：仅用于联调管道与无 Ollama 环境；语义检索不可用。"""

    def __init__(self, dim: int = 768) -> None:
        self._dim = dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vecs: list[list[float]] = []
        for t in texts:
            h = hashlib.sha256(t.encode("utf-8")).digest()
            need_bytes = self._dim * 4
            buf = (h * (need_bytes // len(h) + 1))[:need_bytes]
            ints = [int.from_bytes(buf[i : i + 4], "little") for i in range(0, need_bytes, 4)]
            vec = [((x % 2000) - 1000) / 1000.0 for x in ints]
            # L2 归一化，便于与余弦距离习惯一致
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            vecs.append([v / norm for v in vec])
        return vecs


def get_embedder(backend: str, *, ollama_base_url: str, ollama_model: str, fake_dim: int) -> Embedder:
    b = backend.lower().strip()
    if b == "ollama":
        log.info("嵌入后端：Ollama model=%s base=%s", ollama_model, ollama_base_url)
        return OllamaEmbedder(ollama_base_url, ollama_model)
    if b == "fake":
        log.warning("嵌入后端：fake（无语义，仅验证索引管道）dim=%s", fake_dim)
        return FakeEmbedder(fake_dim)
    raise ValueError(f"未知 KNOW_ME_EMBED_BACKEND：{backend!r}，可选 ollama / fake")
