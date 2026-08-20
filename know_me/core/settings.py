"""
Know Me 运行时配置（E01 建索引 + E02 检索与生成）。

OpenAI 兼容网关（同一主机常见部署）
------------------------------------
- `KNOW_ME_OPENAI_BASE_URL`：须含 **`/v1`**，例如 `http://127.0.0.1:1234/v1`。
- **嵌入**：`POST {base}/embeddings`，模型 `KNOW_ME_OPENAI_EMBED_MODEL`（建索引 + 检索问句必须一致）。
- **对话**：`POST {base}/chat/completions`，模型 `KNOW_ME_OPENAI_CHAT_MODEL`（仅 RAG 生成需要）。
- **鉴权**：`KNOW_ME_OPENAI_API_KEY`；LM Studio 若要求占位符，常见为 `lm-studio`；无鉴权可留空。

字段旁注释为对应环境变量名；复制仓库根目录 `.env.example` 为 `.env` 后按需填写。

E03（HTTP API）
---------------
- `KNOW_ME_CHAT_HISTORY_MAX_TURNS`：默认 6。
- `KNOW_ME_CHAT_SQLITE_ENABLED` / `KNOW_ME_CHAT_SQLITE_PATH`：是否将会话 **user/assistant** 轮次写入 SQLite（默认开启，路径默认 `data/chat.sqlite`）；设 `KNOW_ME_CHAT_SQLITE_ENABLED=0` 则回退为进程内内存，重启即丢。
- `KNOW_ME_AGENT_CONTEXT_CHAR_BUDGET`：发往对话网关的 `messages` 总长度粗估上限（JSON 字符量，含 system）；本地小上下文模型可调低（如 6000）。
- `KNOW_ME_AGENT_CONTEXT_WINDOW_TURNS`：Agent 滑动窗口保留的最近原文轮数；更早内容进入会话摘要。
- `KNOW_ME_AGENT_SUMMARY_*`：会话摘要；`KNOW_ME_AGENT_SUMMARY_MODE` 默认 `lazy`（按需），非每轮结束后自动跑。
- `KNOW_ME_AGENT_TOOL_RESULT_MAX_CHARS`：单次 `search_personal_knowledge` 写入 tool 消息的正文上限，超出则截断。
- **前 N 轮快会话（E03）**：`KNOW_ME_AGENT_FAST_SESSION_TURNS` 等；用于压缩检索与工具上下文以**尽量**缩短延迟；**整轮 <2s 仍依赖对话/嵌入网关与模型速度**，可选 `KNOW_ME_AGENT_FAST_LLM_TIMEOUT_SEC` 硬超时（超时则请求失败）。
- `KNOW_ME_INGEST_API_KEY`：`POST /ingest` 必填的 Bearer 密钥；未设置则该路由不可用。

E04（HR 初筛）
-------------
- `KNOW_ME_DISCLAIMER`：对外展示的可配置免责声明（页脚/开场）；可为空。
- `KNOW_ME_RAG_HR_BOOST`：识别 HR 类问句后是否对 `hr_faq` / `hr_screening` 语料加权检索（默认 true；设 0/false 关闭）。

E05（观测与质量）
----------------
- `KNOW_ME_STRUCTURED_TRACE=1`：向 stderr 输出单行 JSON 运行追踪（KM-501）。
- `KNOW_ME_FEEDBACK_ENABLED` / `KNOW_ME_FEEDBACK_LOG`：`POST /feedback` 与本地 JSONL 路径（KM-503）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    """读整型环境变量；未设置或空串则返回 default。"""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    """读浮点环境变量；未设置或空串则返回 default。"""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _env_optional_float(name: str) -> float | None:
    """读浮点环境变量；未设置或空串则返回 None。"""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    return float(raw)


def _env_bool(name: str, default: bool) -> bool:
    """读布尔环境变量；未设置或空串则返回 default。"""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    v = raw.strip().lower()
    if v in ("1", "true", "yes", "on", "y"):
        return True
    if v in ("0", "false", "no", "off", "n"):
        return False
    return default


def _norm_summary_mode(raw: str | None) -> str:
    """`lazy` 按需（默认）；`after_turn` 每轮结束后；`off` 关闭。"""
    m = (raw or "lazy").strip().lower()
    if m in ("off", "disabled", "none", "0", "false"):
        return "off"
    if m in ("after_turn", "eager", "always", "each_turn"):
        return "after_turn"
    return "lazy"


@dataclass(frozen=True)
class IndexSettings:
    """
    建索引、检索、RAG 共用的配置（由 `IndexSettings.from_env` 与环境变量、CLI 路径选项合并）。

    说明：类名仍为 IndexSettings 是历史原因；其中已包含 E02 所需字段，避免拆成多个 settings 文件。
    """

    # KNOW_ME_CORPUS_ROOT：语料根路径；其下每个一级子目录递归收录 *.md（自动扫描，不限固定目录名；CLI --corpus-root 优先）
    corpus_root: Path
    # KNOW_ME_CHROMA_PATH（CLI --chroma-path 优先；检索与建索引必须指向同一目录）
    chroma_path: Path
    # KNOW_ME_CHUNK_SIZE / KNOW_ME_CHUNK_OVERLAP：切分窗口；PRD 建议约 512 / 50
    chunk_size: int
    chunk_overlap: int
    # KNOW_ME_CHROMA_COLLECTION：Chroma 集合名
    collection_name: str
    # KNOW_ME_OPENAI_BASE_URL：须含 /v1，例如 http://127.0.0.1:1234/v1
    openai_base_url: str
    # KNOW_ME_OPENAI_API_KEY：Bearer Token；本地无鉴权可空
    openai_api_key: str
    # KNOW_ME_OPENAI_EMBED_MODEL：嵌入模型 id（建索引与检索问句必须一致，否则语义检索无效）
    openai_embed_model: str
    # KNOW_ME_OPENAI_EMBED_BATCH_SIZE：嵌入 API 每请求最多多少条文本
    openai_embed_batch_size: int
    # KNOW_ME_OPENAI_CHAT_MODEL：对话模型 id（know-me query 必填）
    openai_chat_model: str
    # KNOW_ME_RAG_TOP_K：默认检索条数（向量近邻数量）
    rag_top_k: int
    # KNOW_ME_LLM_TEMPERATURE：对话采样温度；RAG 建议偏低以增强对证据的忠实度
    llm_temperature: float
    # KNOW_ME_CHAT_HISTORY_MAX_TURNS：E03 API 多轮会话保留的「轮」数（一轮 ≈ user + assistant 各一条）
    chat_history_max_turns: int
    # KNOW_ME_CHAT_SQLITE_ENABLED：是否将会话写入 SQLite（默认 true）
    chat_sqlite_enabled: bool
    # KNOW_ME_CHAT_SQLITE_PATH：会话库文件路径（默认 data/chat.sqlite）
    chat_sqlite_path: Path
    # KNOW_ME_AGENT_CONTEXT_CHAR_BUDGET：Agent 发往 chat.completions 的 messages 粗估字符上限（防本地网关 context 溢出）
    agent_context_char_budget: int
    # KNOW_ME_AGENT_CONTEXT_WINDOW_TURNS：Agent 可见的最近原文轮数（滑动窗口）
    agent_context_window_turns: int
    # KNOW_ME_AGENT_SUMMARY_ENABLED：是否将滑出窗口的对话合并为会话摘要
    agent_summary_enabled: bool
    agent_summary_mode: str
    agent_summary_max_chars: int
    agent_summary_rag_enabled: bool
    agent_summary_rag_max_chars: int
    agent_summary_min_transcript_chars: int
    agent_summary_timeout_sec: float
    # KNOW_ME_AGENT_SYSTEM_AUTO_SLIM：固定上下文（system+tools）接近预算时自动改用精简 system
    agent_system_auto_slim: bool
    # KNOW_ME_AGENT_TOOL_RESULT_MAX_CHARS：检索 tool 回注正文最大字符数
    agent_tool_result_max_chars: int
    # KNOW_ME_INGEST_API_KEY：E03 `POST /ingest` 的 Bearer 密钥；留空则禁用入库接口（返回 503）
    ingest_api_key: str
    # KNOW_ME_DISCLAIMER：E04 对外免责声明（API / CLI 透出；可写入系统提示外的页脚）
    disclaimer_text: str
    # KNOW_ME_RAG_HR_BOOST：E04 对 HR 意图问句是否优先展示 hr_* 语料命中（KM-403）
    rag_hr_boost: bool
    # KNOW_ME_STRUCTURED_TRACE：E05 是否输出 JSON 追踪行到 stderr（KM-501）
    structured_trace_enabled: bool
    # KNOW_ME_FEEDBACK_ENABLED：E05 是否启用 POST /feedback（KM-503）
    feedback_enabled: bool
    # KNOW_ME_FEEDBACK_LOG：反馈 JSONL 文件路径
    feedback_log_path: Path
    # KNOW_ME_AGENT_FAST_SESSION_TURNS：会话内前若干「已完成轮」之后的新提问仍视为快会话窗口（0=关闭）；一轮=user+assistant 各一条
    agent_fast_session_turns: int
    # KNOW_ME_AGENT_FAST_TOP_K：快会话窗口内检索 top_k 上限（与请求/默认取 min）
    agent_fast_top_k: int
    # KNOW_ME_AGENT_FAST_DISABLE_HR_BOOST：快会话窗口内是否关闭 HR 加权的大批量 n_fetch
    agent_fast_disable_hr_boost: bool
    # KNOW_ME_AGENT_FAST_TOOL_RESULT_MAX_CHARS：快会话窗口内 tool 回注正文上限（与 KNOW_ME_AGENT_TOOL_RESULT_MAX_CHARS 取 min 使用）
    agent_fast_tool_result_max_chars: int
    # KNOW_ME_AGENT_FAST_MAX_TOOL_ROUNDS：快会话窗口内工具循环最大轮次（与内置上限取 min）
    agent_fast_max_tool_rounds: int
    # KNOW_ME_AGENT_FAST_LLM_TIMEOUT_SEC：快会话窗口内 chat 请求超时秒数；未设置则与默认 120s 一致
    agent_fast_llm_timeout_sec: float | None

    @staticmethod
    def from_env(corpus_root: Path | None = None, chroma_path: Path | None = None) -> "IndexSettings":
        """组装配置：CLI 传入的 corpus_root / chroma_path 优先于环境变量。"""
        root = corpus_root or Path(os.environ.get("KNOW_ME_CORPUS_ROOT", "corpus")).resolve()
        chroma = chroma_path or Path(os.environ.get("KNOW_ME_CHROMA_PATH", "data/chroma")).resolve()
        tool_cap = max(512, _env_int("KNOW_ME_AGENT_TOOL_RESULT_MAX_CHARS", 4800))
        fast_tool_cap = max(512, _env_int("KNOW_ME_AGENT_FAST_TOOL_RESULT_MAX_CHARS", 2400))
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
            chat_history_max_turns=max(1, _env_int("KNOW_ME_CHAT_HISTORY_MAX_TURNS", 6)),
            chat_sqlite_enabled=_env_bool("KNOW_ME_CHAT_SQLITE_ENABLED", True),
            chat_sqlite_path=Path(os.environ.get("KNOW_ME_CHAT_SQLITE_PATH", "data/chat.sqlite")).expanduser(),
            agent_context_char_budget=max(4096, _env_int("KNOW_ME_AGENT_CONTEXT_CHAR_BUDGET", 12000)),
            agent_context_window_turns=max(1, _env_int("KNOW_ME_AGENT_CONTEXT_WINDOW_TURNS", 3)),
            agent_summary_enabled=_env_bool("KNOW_ME_AGENT_SUMMARY_ENABLED", True),
            agent_summary_mode=_norm_summary_mode(os.environ.get("KNOW_ME_AGENT_SUMMARY_MODE")),
            agent_summary_max_chars=max(200, _env_int("KNOW_ME_AGENT_SUMMARY_MAX_CHARS", 900)),
            agent_summary_rag_enabled=_env_bool("KNOW_ME_AGENT_SUMMARY_RAG_ENABLED", False),
            agent_summary_rag_max_chars=max(512, _env_int("KNOW_ME_AGENT_SUMMARY_RAG_MAX_CHARS", 2400)),
            agent_summary_min_transcript_chars=max(
                0, _env_int("KNOW_ME_AGENT_SUMMARY_MIN_TRANSCRIPT_CHARS", 120),
            ),
            agent_summary_timeout_sec=max(15.0, _env_float("KNOW_ME_AGENT_SUMMARY_TIMEOUT_SEC", 90.0)),
            agent_system_auto_slim=_env_bool("KNOW_ME_AGENT_SYSTEM_AUTO_SLIM", True),
            agent_tool_result_max_chars=tool_cap,
            ingest_api_key=os.environ.get("KNOW_ME_INGEST_API_KEY", "").strip(),
            disclaimer_text=os.environ.get("KNOW_ME_DISCLAIMER", "").strip(),
            rag_hr_boost=_env_bool("KNOW_ME_RAG_HR_BOOST", True),
            structured_trace_enabled=_env_bool("KNOW_ME_STRUCTURED_TRACE", False),
            feedback_enabled=_env_bool("KNOW_ME_FEEDBACK_ENABLED", False),
            feedback_log_path=Path(os.environ.get("KNOW_ME_FEEDBACK_LOG", "data/feedback.jsonl")).expanduser(),
            agent_fast_session_turns=max(0, _env_int("KNOW_ME_AGENT_FAST_SESSION_TURNS", 10)),
            agent_fast_top_k=max(1, _env_int("KNOW_ME_AGENT_FAST_TOP_K", 3)),
            agent_fast_disable_hr_boost=_env_bool("KNOW_ME_AGENT_FAST_DISABLE_HR_BOOST", True),
            agent_fast_tool_result_max_chars=min(tool_cap, fast_tool_cap),
            agent_fast_max_tool_rounds=max(1, min(10, _env_int("KNOW_ME_AGENT_FAST_MAX_TOOL_ROUNDS", 3))),
            agent_fast_llm_timeout_sec=_env_optional_float("KNOW_ME_AGENT_FAST_LLM_TIMEOUT_SEC"),
        )
