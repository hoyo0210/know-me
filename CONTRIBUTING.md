# 贡献指南 (Contributing to Know Me)

感谢你对 **Know Me** 项目的关注与支持！我们欢迎各种形式的贡献，包括但不限于报告 Bug、改进文档、优化检索与提示策略、新增向量数据库适配器以及提交功能代码。

在提交任何代码或 Pull Request（PR）之前，请花几分钟阅读以下指南。

---

## 1. 核心原则与隐私规范（重要）

**Know Me** 定位于个人数字分身（Personal Digital Twin）参考实现。为了保护个人隐私并保证开源仓库的纯净：

1. **严禁在 PR 中提交真实的个人语料或隐私数据**：
   - PR 中涉及测试语料、人设、评测用例时，**只能使用或修改**脱敏占位目录：`corpus.example/`、`persona.example/`、`eval.example/`。
   - 严禁提交真实个人的简历、身份证件、私人联系方式、未公开的薪资期望或内部商业文档。
   - 本地生成的 `corpus/`、`persona/`、`eval/`、`data/`（Chroma 向量库与 SQLite 数据库）已被 `.gitignore` 忽略，请确保不要强制添加到 Git。
2. **严禁在框架默认代码中硬编码个人专属域名或个人标识**：
   - 运行时默认配置必须是通用的占位符或空字符串（如 `KNOW_ME_RESUME_BROWSER_URL` 默认应为空）。

---

## 2. 本地开发环境准备

### 环境要求
- **Python**: $\ge 3.10$
- **Git**
- 兼容 OpenAI 协议的本地或远程模型服务（如 [LM Studio](https://lmstudio.ai/)、[Ollama](https://ollama.com/)、[vLLM](https://github.com/vllm-project/vllm) 或 OpenAI 官方 API）。

### 开发环境搭建

```bash
# 1. Fork 并克隆代码仓库
git clone https://github.com/<your-username>/know-me.git
cd know-me

# 2. 创建并激活 Python 虚拟环境
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. 安装开发模式依赖（包含 pytest 等测试工具）
pip install -e ".[dev]"

# 4. 配置本地环境变量
cp .env.example .env
# 编辑 .env 配置你的本地或测试模型连接信息
```

---

## 3. 开发流程与代码规范

### 分支管理
- 基于 `main` 分支拉取新的特性或修复分支：
  - 新功能：`feature/your-feature-name`
  - 问题修复：`fix/issue-description`
  - 文档改进：`docs/doc-topic`

### 代码风格
- 遵循 **PEP 8** 规范与 Python 现代类型注解（Type Hints）。
- 保持函数与模块职责单一，不引入不必要的大型第三方依赖。
- 修改代码时优先保持现有架构风格一致性，在关键流程处添加必要的类型注释。

### 运行自动化测试

在提交修改前，请确保本地所有测试用例均通过：

```bash
# 运行全部测试
pytest

# 运行特定测试文件
pytest tests/test_no_personal_defaults.py
pytest tests/test_resume_url_defaults.py
```

---

## 4. 提交 Pull Request (PR)

1. **Commit 信息规范**：
   推荐遵循 [Conventional Commits](https://www.conventionalcommits.org/) 规范，例如：
   - `feat: add Milvus vector store backend`
   - `fix: correct token calculation in agent context window`
   - `docs: update docker compose quickstart instructions`
   - `test: add unit tests for hr screening intent detector`

2. **PR 描述要求**：
   - 清晰描述本次 PR 解决的问题与改动方案（What & Why）。
   - 列出关联的 Issue 编号（如有，如 `Fixes #12`）。
   - 包含本地验证结果或测试用例执行输出。

3. **CI 检查**：
   - 提交 PR 后，GitHub Actions CI 将自动运行代码检查与单元测试，请确保所有 Check 均呈绿色通过状态。

---

## 5. 社区交流与行为准则

- 请在讨论中保持友好、包容与互相尊重。
- 如果发现安全漏洞，请不要公开发布 Issue，请参考 [SECURITY.md](SECURITY.md) 中的指引进行私密报告。
