# 最短上线（已有 ECS + 域名 + OSS + DashScope Key）

把下面占位符换成你的值后，按顺序执行。

| 占位符 | 含义 | 你的值 |
|--------|------|--------|
| `CHAT_HOST` | 聊天域名 | 例如 `chat.lihaoxu.cn` |
| `SITE_HOST` | 简历站域名 | 例如 `lihaoxu.cn` |
| `ECS_IP` | ECS 公网 IP | |
| `DASHSCOPE_API_KEY` | 通义 API Key | |
| `OSS_BUCKET` | OSS Bucket 名 | |
| `OSS_ENDPOINT` | 如 `oss-cn-shenzhen.aliyuncs.com` | |
| `OSS_AK` / `OSS_SK` | 仅本机上传用，勿提交 Git | |

---

## A. ECS：聊天服务（约 20 分钟）

### A1. 本机 SSH 上去

```bash
ssh root@ECS_IP   # 或你的登录用户
```

### A2. 安装 Docker（若尚未安装）

```bash
# Ubuntu 示例
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker
```

### A3. 放置项目与数据

```bash
mkdir -p /opt/know-me/{corpus,persona,data}
cd /opt/know-me
git clone https://github.com/hoyo0210/know-me.git src
cd src
cp deploy/docker-compose.prod.yml.example /opt/know-me/docker-compose.yml
```

把**真实**语料、人设拷到 ECS（不要用 example 上生产）：

```bash
# 在你自己的电脑上 scp（路径按实际改）
scp -r "/path/to/local/corpus/" root@ECS_IP:/opt/know-me/corpus/
scp -r "/path/to/local/persona/" root@ECS_IP:/opt/know-me/persona/
```

### A4. 写 `/opt/know-me/.env`（权限 600）

```bash
cat >/opt/know-me/.env <<'EOF'
KNOW_ME_OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
KNOW_ME_OPENAI_API_KEY=DASHSCOPE_API_KEY
KNOW_ME_OPENAI_CHAT_MODEL=qwen-plus
KNOW_ME_OPENAI_EMBED_MODEL=text-embedding-v3
KNOW_ME_RESUME_BROWSER_URL=https://SITE_HOST
KNOW_ME_DISCLAIMER=本助手基于授权公开资料回答，不构成录用或薪酬承诺。
EOF
chmod 600 /opt/know-me/.env
```

把 `DASHSCOPE_API_KEY`、`SITE_HOST` 换成真值（上面 heredoc 里请手动改，或改完再 `chmod`）。

### A5. 启动并建索引

```bash
cd /opt/know-me
# compose 文件里 build context 要指向源码目录：编辑 docker-compose.yml
#   build: ./src
# 或把 compose 放进 src 并改 volume 路径为 ../corpus 等

docker compose -f docker-compose.yml build
docker compose up -d
docker compose exec know-me know-me build-index
curl -sf http://127.0.0.1:8000/health
```

### A6. Nginx + 证书

```bash
# 安装 nginx 与证书后：
cp /opt/know-me/src/deploy/nginx-know-me.conf.example /etc/nginx/conf.d/know-me.conf
# 编辑 server_name = CHAT_HOST，填好 ssl 路径
nginx -t && systemctl reload nginx
curl -sf https://CHAT_HOST/health
```

DNS：`CHAT_HOST` 的 A 记录 → `ECS_IP`。

---

## B. OSS：简历站（约 15 分钟）

### B1. 本机构建（showcase 仓）

```bash
cd "/Users/hoyo/know me/know-me-showcase-export/resume-site"
# 或: git clone git@github.com:hoyo0210/know-me-showcase.git && cd know-me-showcase/resume-site

cat >.env.production <<EOF
VITE_CHAT_URL=https://CHAT_HOST
EOF
npm ci
npm run build
```

### B2. 上传到 OSS

```bash
# 已安装 ossutil 且配置好 AK
ossutil cp -r dist/ oss://OSS_BUCKET/ --endpoint OSS_ENDPOINT --update
```

OSS 控制台：开启静态网站，默认首页 `index.html`；绑定 `SITE_HOST`；建议开 CDN + HTTPS。

DNS：`SITE_HOST` CNAME → CDN/OSS 给定域名。

### B3. 互链检查

- 打开 `https://SITE_HOST` → 「对话」应到 `https://CHAT_HOST`
- 打开 `https://CHAT_HOST` → 「简历」应到 `https://SITE_HOST`

---

## C. 常见问题

| 现象 | 处理 |
|------|------|
| 聊天一直转圈 / 断流 | Nginx `proxy_read_timeout 300s`、`proxy_buffering off` |
| 检索空 / 乱答 | 是否对真实 corpus 跑过 `build-index`；embed 模型是否与建索引一致 |
| 简历点对话 404 | `VITE_CHAT_URL` 是否在 **build 前**写入 `.env.production` |
| DashScope 401 | Key、地域 endpoint 是否匹配控制台 |

---

填好域名后把 **`CHAT_HOST` / `SITE_HOST`**（可打码 Key）发给助手，可生成一份已替换占位符的专用命令清单。
