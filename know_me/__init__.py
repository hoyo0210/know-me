"""
Know Me — 个人数字分身：E01 索引管道 + E02 RAG + E03 HTTP Agent API + E04 HR 初筛 + E05 观测与评测。

包内目录（建议阅读顺序）
--------------------------------
**E01（语料 → 向量库）** — `know_me.index`
1. `core.settings` / `core.types` — 配置与数据结构
2. `index.loaders` / `index.splitting` — 读盘与切块
3. `index.embeddings` / `index.chroma_store` / `index.pipeline` — 嵌入与落盘

**E02（问句 → 检索 → 回答）** — `know_me.rag`
1. `core.types_rag` — 命中条与最终回答结构
2. `rag.retrieval` — Chroma 向量检索（KM-201）
3. `persona.loader`（本地 `persona/` 或 `KNOW_ME_PERSONA_DIR`；示例见 `persona.example/`）+ `rag.prompts` + `rag.llm` — 人设与对话 HTTP（与嵌入共用 Base URL）
4. `rag.rag_answer` — 编排：retrieve → 拼证据 → chat（KM-202）
5. `cli` — `build-index` / `query` / `chat` / `serve` / `version`

**E03（多轮对话 + Agent + 流式 API）** — `know_me.agent` / `know_me.api`
1. `agent.sessions` — 进程内会话（KM-301）
2. `agent.prompts_agent` + `agent.agent_chat` — 工具循环与 SSE 事件（KM-302 / KM-303）
3. `api.app` — `GET /`（Web 聊天）、`GET /health`、`POST /chat`、`POST /ingest`（KM-304）
4. `cli` — `serve`

**E04（HR 初筛）**
- `core.settings`：`KNOW_ME_DISCLAIMER`、`KNOW_ME_RAG_HR_BOOST`
- `rag.retrieval`：`is_hr_intent` + hr 语料优先重排（KM-403）
- `rag.prompts` / `agent.prompts_agent`：本地 `persona/`（或 `KNOW_ME_PERSONA_DIR`）人设文件 + 薪酬与敏感信息边界（KM-402）
- `agent.agent_chat` / `api.app` / `cli chat`：免责声明透出

**E05（观测与质量）** — `know_me.observability`
- `observability.trace_log` + `KNOW_ME_STRUCTURED_TRACE`：RAG / Agent 单行 JSON 追踪（KM-501）
- `observability.eval_run` + `know-me eval` + 本地 `eval/cases.jsonl`：分桶回归报告（KM-502）
- `POST /feedback` + `message_id` + `KNOW_ME_FEEDBACK_ENABLED`（KM-503）

E02 数据流（与代码对应）
------------------------
  用户问句 → rag.retrieval（问句嵌入 + Chroma.query）
          → rag.rag_answer（拼「依据片段」+ 系统提示）
          → rag.llm（LangChain ChatOpenAI → OpenAI 兼容 chat.completions）
          → RAGAnswer（正文 + citations + retrieved）

E03 数据流（与代码对应）
------------------------
  HTTP POST /chat → agent.sessions（历史裁剪）→ agent.agent_chat（tools 循环 + 流式正文）
                 → rag.llm（LangChain：bind_tools / invoke / stream）
                 → rag.retrieval（search_personal_knowledge 工具）
"""

__version__ = "1.0.0"
