"""
嵌入（Embedding）：把「可变长文本」映射为「固定维度的浮点向量」，供向量库建索引与检索。

实现策略（与你的部署一致）：
- 仅使用 **OpenAI 兼容** 的 HTTP 接口：`POST {base_url}/embeddings`
- `base_url` 通常为 `http://127.0.0.1:1234/v1`（LM Studio）、或兼容网关提供的 `/v1` 根路径
- 本地 llama.cpp 若通过 **llama-server 且启用 OpenAI 兼容嵌入**，同样走此路径

本模块在架构中的位置：
- E01：Chroma 写入时通过 EmbeddingFunction 回调本客户端
- E02：`retrieval.retrieve` 再次 `build_embedder` 并 `get_or_create_collection`，
  用 `query_texts` 对问句嵌入；必须与建索引时 **同一 base_url + embed_model**，否则近邻无意义
"""

from __future__ import annotations

import logging
from typing import Protocol, Sequence, runtime_checkable

import httpx

from know_me.settings import IndexSettings

log = logging.getLogger(__name__)

_HTTP_ERR_BODY_MAX = 800


def _http_error_detail(resp: httpx.Response) -> str:
    """截取响应体便于排障（401/403 时常含具体原因）。"""
    raw = (resp.text or "").strip()
    if len(raw) > _HTTP_ERR_BODY_MAX:
        return raw[:_HTTP_ERR_BODY_MAX] + " …"
    return raw


def _raise_embeddings_http(resp: httpx.Response) -> None:
    """将非 2xx 转为带截断响应体与排障提示的 RuntimeError（便于区分鉴权/路径错误）。"""
    if resp.is_success:
        return
    body = _http_error_detail(resp)
    hint = (
        "请核对 KNOW_ME_OPENAI_BASE_URL、KNOW_ME_OPENAI_API_KEY：须与服务端 OpenAI 兼容网关一致；"
        "LM Studio 文档常用占位为 lm-studio（与 OpenAI(base_url=..., api_key='lm-studio') 等价）；"
        "若服务端未开启鉴权，可将 KNOW_ME_OPENAI_API_KEY 留空。"
    )
    raise RuntimeError(f"嵌入接口 HTTP {resp.status_code} {resp.reason_phrase}。{hint} 响应体：{body or '(空)'}")


@runtime_checkable
class Embedder(Protocol):
    """嵌入后端协议（当前仅 OpenAI 兼容实现；将来若增加进程内后端可再实现本协议）。"""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """输入多条文本，返回等长的向量列表；每条向量维度必须一致。"""
        ...


class OpenAICompatibleEmbedder:
    """
    OpenAI Embeddings API 兼容客户端。

    请求：`POST {base_url}/embeddings`，JSON `{"model": "...", "input": [...]}`。
    响应：解析 `data[].embedding`，按 `index` 排序后与输入顺序对齐。

    鉴权：若提供 `api_key`，则设置 `Authorization: Bearer <api_key>`（多数本地服务可留空）。
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 120.0,
        batch_size: int = 32,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._model = model.strip()
        self._api_key = api_key
        self._timeout = timeout
        self._batch_size = max(1, batch_size)

    def _url(self) -> str:
        return f"{self._base}/embeddings"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not self._model:
            raise ValueError(
                "未配置嵌入模型名：请设置环境变量 KNOW_ME_OPENAI_EMBED_MODEL（须与 LM Studio / 服务端中模型 id 一致）",
            )
        texts_list = list(texts)
        if not texts_list:
            return []

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        out: list[list[float]] = []
        with httpx.Client(timeout=self._timeout) as client:
            for i in range(0, len(texts_list), self._batch_size):
                batch = texts_list[i : i + self._batch_size]
                # OpenAI 规范：input 可为 string 或 string[]；批量减少往返
                payload: dict = {"model": self._model, "input": batch}
                r = client.post(self._url(), headers=headers, json=payload)
                _raise_embeddings_http(r)
                data = r.json()
                arr = data.get("data")
                if not isinstance(arr, list) or not arr:
                    raise RuntimeError(f"嵌入接口返回缺少 data：{data!r}")
                pieces = sorted(arr, key=lambda x: int(x.get("index", 0)))
                for item in pieces:
                    emb = item.get("embedding")
                    if not isinstance(emb, list) or not emb:
                        raise RuntimeError(f"嵌入项异常：{item!r}")
                    out.append([float(x) for x in emb])
                if len(out) != i + len(batch):
                    # 若服务端未返回完整条数，尽早失败以免 Chroma 错位
                    raise RuntimeError(
                        f"嵌入返回条数与请求不一致：已累积 {len(out)}，期望本批后 {i + len(batch)}",
                    )
        return out


def build_embedder(settings: IndexSettings) -> Embedder:
    """由 IndexSettings 构造当前唯一的嵌入后端。"""
    if not settings.openai_embed_model.strip():
        raise ValueError(
            "未配置 KNOW_ME_OPENAI_EMBED_MODEL：请填写 LM Studio / 服务端中的嵌入模型 id，"
            "或复制仓库根目录 .env.example 为 .env 后填写。",
        )
    log.info(
        "嵌入：OpenAI 兼容 API base=%s model=%s",
        settings.openai_base_url,
        settings.openai_embed_model,
    )
    return OpenAICompatibleEmbedder(
        base_url=settings.openai_base_url,
        model=settings.openai_embed_model,
        api_key=settings.openai_api_key,
        batch_size=settings.openai_embed_batch_size,
    )
