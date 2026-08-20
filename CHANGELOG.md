# 变更日志 (Changelog)

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式，版本号遵循 [语义化版本 2.0.0](https://semver.org/lang/zh-CN/)。

---

## [1.0.0] - 2026-08-21

### 计划中
- 混合检索（BM25 + Dense 向量检索与 RRF 融合）
- 内置应用层限流中间件（Rate Limiting Middleware）
- 多向量数据库后端适配（Milvus、Qdrant、PGVector）
- PyPI 官方包分发

---

## [1.0.0] - 2026-08-21

### 新增 (Added)
- **RAG 向量索引与检索管道**：
  - 支持多层级 Markdown 语料加载与 YAML front matter 元数据解析。
  - 基于 Chroma 向量数据库的持久化向量存储与相似度检索。
  - 命令行工具 `know-me build-index` 与单次问答 `know-me query`。
- **人设与边界管理系统 (Persona & Soul)**：
  - 支持声明式 Markdown 人设文件 `IDENTITY.md` 与 `SOUL.md`。
  - 支持 `{owner_name}` 动态插值与欢迎语 `session_opening` 设定。
  - 提供了脱敏占位样例目录 `persona.example/` 与 `corpus.example/`。
- **多轮 Agent 对话编排**：
  - 基于 LangChain / LangGraph 编排的对话 Agent 与动态检索工具循环。
  - 智能会话上下文预算控制、滑动窗口（Context Window）与自动精简（Auto Slim）。
  - 支持惰性会话摘要生成与 SQLite 会话持久化。
  - 提供终端交互命令 `know-me chat`。
- **FastAPI HTTP 服务与流式 Web 聊天界面**：
  - 高性能异步 HTTP 服务端（`know-me serve`），支持 `POST /chat` 的 SSE 流式响应与心跳保活（Keepalive）。
  - 内置开箱即用的响应式 Web 聊天页（`GET /`），支持头像、Markdown 渲染及动态简历跳转（`KNOW_ME_RESUME_BROWSER_URL`）。
  - 健康检查接口 `GET /health`。
  - 向量库热重建接口 `POST /ingest`，支持基于 API Key 的安全鉴权。
- **HR 招聘初筛与意图增强**：
  - HR 意图弱分类器与针对 `hr_faq`、`hr_screening` 语料的优先重排（HR Boost）。
  - 结构化可引用出处来源及合规免责声明。
- **评测与可观测性套件**：
  - 自动化 JSONL 评测工具 `know-me eval` 及脱敏评测用例 `eval.example/`。
  - 单行 JSON 结构化运行追踪（Structured Trace）与可选的用户反馈收集（`POST /feedback`）。
- **容器化部署与工程化支持**：
  - 提供标准 `Dockerfile` 与 `docker-compose.yml`，支持一键容器化启动。
  - 完善的 `.env.example` 环境变量示例及单元测试套件。
