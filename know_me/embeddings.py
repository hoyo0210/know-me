"""
嵌入（Embedding）：把「可变长文本」映射为「固定维度的浮点向量」，供向量库建索引与检索。

本模块在架构中的位置：
- E01：写入时，Chroma 会调用包装后的 Embedder，为每条 chunk 计算向量并存盘。
- E02（检索）：查询句也要走同一个模型，才能在同一向量空间里比「谁更近」。

为何要有 FakeEmbedder：
- 真实模型依赖 Ollama / GPU / 下载权重；CI 或初学时先用 fake 验证「加载→切分→写入」全链路。
- fake 向量无语义：相似度搜索的结果没有业务意义，只能测管道是否通畅。

为何 OllamaEmbedder 逐条请求：
- Ollama 的 /api/embeddings 常见用法是单条 prompt；批量优化可后续再加（如并发或批处理 API）。
"""

from __future__ import annotations

import hashlib
import logging
import math
from typing import Protocol, Sequence, runtime_checkable

import httpx

log = logging.getLogger(__name__)


@runtime_checkable
class Embedder(Protocol):
    """嵌入后端协议：对上层屏蔽「到底是 Ollama 还是 fake」。"""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """输入多条文本，返回等长的向量列表；每条向量维度必须一致。"""
        ...


class OllamaEmbedder:
    """
    通过 Ollama HTTP API 调用本地嵌入模型。

    请求体与官方示例一致：POST /api/embeddings，JSON 含 model 与 prompt。
    返回 JSON 中的 embedding 即 float 列表；维度由模型决定（与集合创建时绑定）。
    """

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
    """
    确定性伪向量：同一字符串每次得到相同向量；不同字符串一般正交性/距离无真实语义。

    实现要点：
    - 用 SHA-256 把文本变成字节种子，再展开为 dim 维浮点向量。
    - L2 归一化：使向量落在我们常用「余弦相似度」几何直觉上（模长为 1）。
    """

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
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            vecs.append([v / norm for v in vec])
        return vecs


def get_embedder(backend: str, *, ollama_base_url: str, ollama_model: str, fake_dim: int) -> Embedder:
    """工厂：按字符串选择具体嵌入实现（扩展新后端时在此加分支）。"""
    b = backend.lower().strip()
    if b == "ollama":
        log.info("嵌入后端：Ollama model=%s base=%s", ollama_model, ollama_base_url)
        return OllamaEmbedder(ollama_base_url, ollama_model)
    if b == "fake":
        log.warning("嵌入后端：fake（无语义，仅验证索引管道）dim=%s", fake_dim)
        return FakeEmbedder(fake_dim)
    raise ValueError(f"未知 KNOW_ME_EMBED_BACKEND：{backend!r}，可选 ollama / fake")
