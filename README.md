# Know Me

**Know Me** 是个人数字分身（Personal Digital Twin）的参考实现：把授权语料写入本地向量库，用 **RAG + Agent** 做可引用出处的问答与多轮对话，并覆盖 HR 初筛等场景的提示与检索策略。

## 功能概览

| 代号 | 内容 |
|------|------|
| E01 | 语料加载、切分、嵌入，写入 Chroma（`build-index`） |
| E02 | 向量检索 + 基于片段的 LLM 回答（`query`） |
| E03 | 多轮会话、工具循环、FastAPI + Web 聊天页（`chat` / `serve`） |
| E04 | HR 类意图检索增强、免责声明与敏感话题边界 |
| E05 | 结构化追踪、JSONL 评测回归、`POST /feedback` |

## 环境要求

- Python **≥ 3.10**
- 任意 **OpenAI 兼容** 对话与嵌入接口（例如本地 [LM Studio](https://lmstudio.ai/)），需分别配置对话模型与嵌入模型 id

## 安装

在仓库根目录执行：

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

安装后可使用命令 **`know-me`**（与 `know-me-index` 等价，均进入同一 CLI）。

## 配置

复制示例环境文件并填写模型等变量：

```bash
cp .env.example .env
```

至少设置（含义见 `.env.example` 内注释）：

- `KNOW_ME_OPENAI_BASE_URL` — 须含 `/v1` 的根地址  
- `KNOW_ME_OPENAI_EMBED_MODEL` — 与建索引、检索一致  
- `KNOW_ME_OPENAI_CHAT_MODEL` — 对话模型 id  

可选：`KNOW_ME_OPENAI_API_KEY`、RAG Top-K、子路径部署用的 `KNOW_ME_HTTP_BROWSER_PREFIX` / `KNOW_ME_HTTP_ROOT_PATH` 等。

可选：通过环境变量 **`KNOW_ME_CORPUS_ROOT`** 指定语料根目录（未设置时默认为仓库下的 `corpus/`）。各 CLI 子命令仍可用 **`--corpus-root`** 覆盖。

可选：通过 **`KNOW_ME_PERSONA_DIR`** 指定人设 Markdown 目录；未设置时默认为仓库根下的 **`persona/`**（本地自建，**默认 Git 忽略**）。克隆后从 **`persona.example/`** 复制或把该变量指向示例目录；字段说明见 **`persona.example/README.md`** 与下节。

## 人设（persona）

人设为 **`IDENTITY.md`** 与 **`SOUL.md`**，由本机维护；**`persona/` 默认不纳入 Git**（见 `.gitignore`），请勿将含真实身份与隐私边界的内容推送到远端。

仓库内提供 **脱敏占位**（可提交）：**`persona.example/`**，说明见 **`persona.example/README.md`**。快速初始化本地人设目录：

```bash
mkdir -p persona
cp persona.example/IDENTITY.md persona.example/SOUL.md persona/
# 再按需编辑 persona/*.md
```

也可**不复制**，在环境中设置 **`KNOW_ME_PERSONA_DIR=persona.example`**（或在 `.env` 中配置）以直接加载示例文件、验证 RAG / Agent。

`IDENTITY.md` 的 YAML 头中需设置 **`display_name`**（对外称呼），可选 **`aliases`**（用于问句弱信号）、**`session_opening`**（欢迎语，支持 `{owner_name}`）；正文描述「agent 是谁」。`SOUL.md` 描述三观与行为边界，正文中可用 **`{owner_name}`** 指代本人。

## 语料库（corpus）

语料为 **Markdown**，由本机维护；**`corpus/` 默认不纳入 Git**（见 `.gitignore`），请勿把含隐私或授权范围外的正文推送到远端。

仓库内提供 **脱敏目录与占位正文**（可提交）：**`corpus.example/`**（四类子目录 + 示例 `.md`），说明见 **`corpus.example/README.md`**。快速初始化本地语料目录：

```bash
mkdir -p corpus
cp -R corpus.example/about_me corpus.example/faq corpus.example/hr_faq corpus.example/hr_screening corpus/
# 再按需编辑 corpus/**/*.md 或增删文件
```

也可**不复制**，直接对示例目录建索引以验证管道：

```bash
know-me build-index --corpus-root corpus.example
```

### 目录结构（一级子目录名固定）

程序只扫描下列 **四类** 一级目录（缺失的目录会被跳过，不报错）。其下可任意嵌套子目录，只要扩展名为 **`.md`** 即会参与索引：

```
corpus/
├── about_me/       # 自我介绍、履历要点、技术观点等
├── faq/            # 通用常见问题
├── hr_faq/         # HR / 招聘流程类 FAQ（可选）
└── hr_screening/   # 初筛口径、地点/到岗等可公开说明（可选）
```

文件名与层级可自定，例如 `about_me/intro.md`、`faq/topics.md`。

### 正文与 YAML 头（可选）

每个 `.md` 文件可在正文前使用 **YAML front matter**（`---` … `---`），正文写在第二个 `---` 之后。常用字段（均可省略；省略时部分字段会用文件修改日期或目录类型兜底，见 `know_me/index/splitting.py` 中 `build_chunk_metadata`）：

| 字段 | 说明 |
|------|------|
| `date` | 文档日期（如 `2026-01-15`）；不写则用文件 mtime 的 UTC 日期 |
| `topic` | 主题标签；不写则用该文件所属的一级目录名（`about_me` / `faq` 等） |
| `audience` | 面向对象说明（字符串） |
| `privacy_level` | 默认 `public` |

### 构建向量索引

1. 配置好 `.env` 中的 **`KNOW_ME_OPENAI_EMBED_MODEL`** 及 **`KNOW_ME_OPENAI_BASE_URL`**（以及按需的 API Key）。  
2. 在语料就绪后执行：

```bash
know-me build-index
```

常用选项：

- **`--corpus-root`**：语料根路径（默认 `corpus/` 或环境变量 `KNOW_ME_CORPUS_ROOT`）。  
- **`--chroma-path`**：Chroma 持久化目录（默认 `data/chroma/`）。  
- **`--reset`**：重建前清空已有集合（大改语料或需与磁盘完全一致时使用）。

索引构建完成后，再使用 `query` / `chat` / `serve`；**嵌入模型 id 须与建索引时一致**，否则检索质量会异常。

## 快速开始

### 方式 A：Docker Compose 快速启动（推荐）

```bash
# 1. 构建镜像并后台启动服务（自动加载 corpus.example 与 persona.example）
docker compose build
docker compose up -d

# 2. 检查健康状态（HTTP 200）
curl http://127.0.0.1:8000/health

# 3. 停止服务
docker compose down
```

服务启动后，可在浏览器打开 `http://127.0.0.1:8000/` 访问 Web 聊天界面。

### 方式 B：本地 Python 环境

默认语料目录为 **`corpus/`**，向量数据目录为 **`data/chroma/`**（首次索引会自动创建）。若尚未准备个人语料，可先使用上一节 **`corpus.example/`** 的复制或 `--corpus-root corpus.example`。

```bash
# 1. 构建向量索引（需嵌入模型可用）
know-me build-index

# 2. 单次问答（默认流式输出正文到 stdout）
know-me query "你的问题"

# 3. 终端多轮对话（Agent + 检索工具）
know-me chat

# 4. HTTP 服务：浏览器打开 http://127.0.0.1:8000/ ，API 文档见 /docs
know-me serve --host 127.0.0.1 --port 8000
```

若语料或 Chroma 路径与默认不同，可为各子命令传入 `--corpus-root`、`--chroma-path`。

## HTTP 服务要点

- **`GET /`** — Web 聊天界面（与 API 同源）  
- **`GET /health`** — 健康检查  
- **`POST /chat`** — 多轮对话（默认 SSE 流式）  
- **`POST /ingest`** — 触发索引构建（需配置 ingest 密钥时启用）  
- **`POST /feedback`** — 可选反馈落盘（见 `.env.example`）  

应用部署在**反向代理子路径**下时，请按 `.env.example` 与 `know_me/api/app.py` 文档字符串配置 `KNOW_ME_HTTP_BROWSER_PREFIX`（及必要时 `KNOW_ME_HTTP_ROOT_PATH`），避免前端请求打到错误路径。

## 评测（eval）

评测输入为 **JSONL**（每行一个 JSON 用例），默认 CLI 参数为 **`eval/cases.jsonl`**，该目录由本机维护；**`eval/` 默认不纳入 Git**（见 `.gitignore`），勿将含真实问句或隐私期望的用例推送到远端。克隆后请在本地自建 `eval/` 并编写或拷贝用例文件。

仓库内提供 **脱敏格式示例**（可提交）：**`eval.example/cases.sample.jsonl`**，说明见 **`eval.example/README.md`**。快速初始化本地评测文件：

```bash
mkdir -p eval && cp eval.example/cases.sample.jsonl eval/cases.jsonl
# 再按个人语料改写 question / expect_keywords
```

也可直接指定样例路径（仍需已完成 `build-index` 且模型可用）：

```bash
know-me eval --cases eval.example/cases.sample.jsonl
# 或（使用本地 eval 副本时）：
know-me eval --cases eval/cases.jsonl
```

可通过 **`--cases`** 指向任意路径，用 **`--out`** 指定报告 JSON 输出路径；详细字段见 `know_me/observability/eval_run.py`。

## 仓库结构（节选）

| 路径 | 说明 |
|------|------|
| `know_me/` | Python 包：索引管道、RAG、Agent、API、CLI |
| `corpus.example/` | 语料目录与脱敏占位 `.md`（可随仓库同步） |
| `corpus/` | 个人语料（本地自建；默认 Git 忽略，不随仓库同步） |
| `persona.example/` | 人设 Markdown 脱敏样例与说明（可随仓库同步） |
| `persona/` | 人设 `IDENTITY.md` / `SOUL.md`（本地自建；默认 Git 忽略） |
| `data/` | 向量库、反馈日志等（本地生成；默认 Git 忽略） |
| `eval.example/` | 评测 JSONL 脱敏样例与说明（可随仓库同步） |
| `eval/` | 评测 JSONL 与报告输出（本地；默认 Git 忽略） |

## 版本与许可证

- 包版本：`know-me version` 或 `know_me.__version__`（与 `pyproject.toml` 对齐）  
- 许可证：[MIT](LICENSE)  
