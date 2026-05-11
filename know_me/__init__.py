"""
Know Me — 个人数字分身：E01 索引管道 + E02 RAG（检索 + 生成）。

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
5. `cli` — `build-index` / `query` / `version`

E02 数据流（与代码对应）
------------------------
  用户问句 → retrieval（问句嵌入 + Chroma.query）
          → rag_answer（拼「依据片段」+ 系统提示）
          → llm（POST /v1/chat/completions）
          → RAGAnswer（正文 + citations + retrieved）
"""

__version__ = "0.1.0"
