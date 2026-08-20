# 安全策略 (Security Policy)

本项目非常重视系统安全性与用户隐私保护。请在阅读和部署 **Know Me** 前仔细阅读本安全政策与生产环境安全指引。

---

## 1. 报告安全漏洞 (Reporting a Vulnerability)

如果你在 **Know Me** 中发现了任何安全漏洞或隐患，**请不要公开创建 GitHub Issue**，以免在漏洞修复前被恶意利用。

请通过以下方式提交私密报告：
- **GitHub 私密漏洞报告（推荐）**：进入本仓库的 `Security` 选项卡，点击 `Report a vulnerability`（Advisories）提交。
- **邮件联系**：将漏洞详情、复现步骤及受影响版本发送至维护者安全邮箱或通过私密渠道联系项目作者。

收到漏洞报告后，我们将尽快完成评估、确认复现并发布修复版本及安全公告。

---

## 2. 生产部署安全指南 (Production Security Best Practices)

作为开源参考实现，Know Me 定位为轻量级应用服务层。在将服务部署到公网或生产环境时，运营者**必须**采取以下安全防护措施：

### 2.1 公开 `/chat` 与 Web 接口防滥用（速率限制与 WAF）
- **核心风险**：大语言模型（LLM）调用和向量检索均为计算与成本密集型操作。若将 `POST /chat` 接口或前端页面无限制暴露在公网，极易遭遇恶意刷量、DDoS 攻击、恶意消耗 LLM Token 或导致服务拒绝。
- **必要措施**：
  - **必须在应用前端架设反向代理与网关**（例如 Nginx、Cloudflare、Traefik、Kong、AWS WAF 等）。
  - 配置基于客户端 IP、会话 ID 或 Token 的**严格速率限制（Rate Limiting）**（例如限制每个 IP 每分钟不超过 10 次对话请求）。
  - 开启 Web 应用防火墙（WAF）规则以拦截自动化爬虫、Prompt 注入扫描和常见恶意 Payload。
  - 在反向代理层设置合理的请求超时时间（如 `proxy_read_timeout 300s`，配合服务端的 SSE Keepalive）。

### 2.2 保护索引构建接口 (`POST /ingest`)
- **核心风险**：`POST /ingest` 接口会触发全量语料重新加载与向量索引重建，属于高开销写操作。
- **必要措施**：
  - 必须配置环境变量 **`KNOW_ME_INGEST_API_KEY`** 设置高强度的密钥。
  - 客户端请求 `POST /ingest` 时需携带请求头 `Authorization: Bearer <your_api_key>`。
  - 若未配置 `KNOW_ME_INGEST_API_KEY`，服务端默认直接返回 `503 Service Unavailable` 拒绝执行。
  - 建议在反向代理或防火墙层将 `/ingest` 路径限制在内网、本地回环或受限管理 IP 访问。

### 2.3 语料库与人设数据隐私 (Corpus & Persona Privacy)
- **核心风险**：个人语料可能包含敏感的职业履历、个人联系方式、内部设计文档或隐私边界设定。
- **必要措施**：
  - 仓库默认已在 `.gitignore` 中忽略了 `corpus/`、`persona/`、`eval/` 和 `data/` 目录。
  - 在配置个人数据并推送到公开代码托管平台前，务必二次确认未将包含真实隐私的文档提交入版本控制。
  - 向量数据库存储目录（如 `data/chroma/`）和聊天历史数据库（如 `data/chat.sqlite`）应赋予最小文件读写权限。

### 2.4 反向代理与跨域策略 (CORS & Root Path)
- 在多域名或前后端分离架构中，务必通过 `KNOW_ME_CORS_ORIGINS` 限制允许的跨域来源。
- 当部署在 URL 子路径（如 `https://example.com/knowme/`）时，请正确设置 `KNOW_ME_HTTP_BROWSER_PREFIX` 和 `KNOW_ME_HTTP_ROOT_PATH`，避免静态资源与接口请求路由异常。

---

## 3. 支持的版本 (Supported Versions)

目前仅对最新的主要版本提供安全修复和补丁支持：

| 版本 | 支持状态 |
| :--- | :--- |
| `1.0.x` | :white_check_mark: 积极支持中 |
| `< 1.0` | :x: 不再支持 |
