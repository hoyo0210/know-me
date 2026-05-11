"""
Know Me — 个人数字分身：E01 索引管道 + E02 RAG + E03 HTTP Agent API + E04 HR 初筛 + E05 观测与评测。

包内模块导读（建议阅读顺序）
--------------------------------
**E01（语料 → 向量库）**
1. `settings` / `types` — 配置与数据结构
2. `loaders` / `splitting` — 读盘与切块
3. `embeddings` / `chroma_store` / `pipeline` — 嵌入与落盘

**E02（问句 → 检索 → 回答）**
1. `types_rag` — 命中条与最终回答结构
2. `retrieval` — Chroma 向量检索（KM-201）
3. `prompts` + `llm` — 系统约束与对话 HTTP（与嵌入共用 Base URL）
4. `rag_answer` — 编排：retrieve → 拼证据 → chat（KM-202）
5. `cli` — `build-index` / `query` / `chat` / `serve` / `version`

**E03（多轮对话 + Agent + 流式 API）**
1. `sessions` — 进程内会话（KM-301）
2. `prompts_agent` + `agent_chat` — 工具循环与 SSE 事件（KM-302 / KM-303）
3. `api_app` — `GET /health`、`POST /chat`、`POST /ingest`（KM-304）
4. `cli` — `serve`

**E04（HR 初筛）**
- `settings`：`KNOW_ME_DISCLAIMER`、`KNOW_ME_RAG_HR_BOOST`
- `retrieval`：`is_hr_intent` + hr 语料优先重排（KM-403）
- `prompts` / `prompts_agent`：薪酬与敏感信息边界（KM-402）
- `agent_chat` / `api_app` / `cli chat`：免责声明透出

**E05（观测与质量）**
- `trace_log` + `KNOW_ME_STRUCTURED_TRACE`：RAG / Agent 单行 JSON 追踪（KM-501）
- `eval_run` + `know-me eval` + `eval/cases.jsonl`：分桶回归报告（KM-502）
- `POST /feedback` + `message_id` + `KNOW_ME_FEEDBACK_ENABLED`（KM-503）

E02 数据流（与代码对应）
------------------------
  用户问句 → retrieval（问句嵌入 + Chroma.query）
          → rag_answer（拼「依据片段」+ 系统提示）
          → llm（POST /v1/chat/completions）
          → RAGAnswer（正文 + citations + retrieved）

E03 数据流（与代码对应）
------------------------
  HTTP POST /chat → sessions（历史裁剪）→ agent_chat（tools 循环 + 流式正文）
                 → llm.chat_complete_with_tools / iter_chat_complete
                 → retrieval（search_personal_knowledge 工具）
"""

__version__ = "0.1.0"
