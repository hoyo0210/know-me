#!/usr/bin/env bash
# Know Me — ECS 一键安装（Docker + Compose + 可选 Nginx/Certbot）
#
# 用法（在 ECS 上，建议 root 或 sudo）：
#
#   curl -fsSL https://raw.githubusercontent.com/hoyo0210/know-me/main/deploy/install-ecs.sh -o install-ecs.sh
#   bash install-ecs.sh
#
# 非交互（提前 export）：
#   export KNOW_ME_CHAT_HOST=chat.example.com
#   export KNOW_ME_SITE_HOST=example.com
#   export KNOW_ME_OPENAI_API_KEY=sk-xxx
#   export KNOW_ME_OPENAI_CHAT_MODEL=qwen-plus
#   export KNOW_ME_OPENAI_EMBED_MODEL=text-embedding-v3
#   export KNOW_ME_INSTALL_NGINX=1          # 0 跳过
#   export KNOW_ME_INSTALL_CERTBOT=1       # 需 80 可从公网访问
#   bash install-ecs.sh
#
# 安装后请把真实 corpus/、persona/ 放到 $INSTALL_DIR，再执行：
#   cd $INSTALL_DIR && docker compose exec know-me know-me build-index
#
set -euo pipefail

REPO_URL="${KNOW_ME_REPO_URL:-https://github.com/hoyo0210/know-me.git}"
REPO_REF="${KNOW_ME_REPO_REF:-main}"
INSTALL_DIR="${KNOW_ME_INSTALL_DIR:-/opt/know-me}"
DASH_BASE_DEFAULT="https://dashscope.aliyuncs.com/compatible-mode/v1"

RED=$'\033[31m'; GRN=$'\033[32m'; YLW=$'\033[33m'; NC=$'\033[0m'
log()  { printf '%s\n' "$*"; }
ok()   { printf '%s%s%s\n' "$GRN" "$*" "$NC"; }
warn() { printf '%s%s%s\n' "$YLW" "$*" "$NC"; }
err()  { printf '%s%s%s\n' "$RED" "$*" "$NC" >&2; }

need_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    err "请使用 root 运行，或: sudo bash $0"
    exit 1
  fi
}

prompt() {
  # prompt VAR "问题" "默认值"
  local var="$1" msg="$2" def="${3:-}"
  local cur="${!var-}"
  if [[ -n "${cur}" ]]; then
    return 0
  fi
  if [[ -n "$def" ]]; then
    read -r -p "$msg [$def]: " cur || true
    cur="${cur:-$def}"
  else
    read -r -p "$msg: " cur || true
  fi
  printf -v "$var" '%s' "$cur"
}

install_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    ok "Docker 与 Compose 已就绪"
    return 0
  fi
  warn "正在安装 Docker…"
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -y
    apt-get install -y ca-certificates curl git
    curl -fsSL https://get.docker.com | sh
  elif command -v yum >/dev/null 2>&1; then
    yum install -y curl git
    curl -fsSL https://get.docker.com | sh
  else
    err "无法自动安装 Docker，请先手动安装 docker + compose 插件"
    exit 1
  fi
  systemctl enable --now docker
  ok "Docker 安装完成"
}

clone_or_update() {
  mkdir -p "$INSTALL_DIR"
  if [[ -d "$INSTALL_DIR/src/.git" ]]; then
    warn "更新已有源码 $INSTALL_DIR/src …"
    git -C "$INSTALL_DIR/src" fetch --depth 1 origin "$REPO_REF"
    git -C "$INSTALL_DIR/src" checkout "$REPO_REF"
    git -C "$INSTALL_DIR/src" pull --ff-only origin "$REPO_REF" || true
  else
    ok "克隆 $REPO_URL ($REPO_REF) → $INSTALL_DIR/src"
    git clone --depth 1 --branch "$REPO_REF" "$REPO_URL" "$INSTALL_DIR/src"
  fi
  mkdir -p "$INSTALL_DIR"/{corpus,persona,data}
}

write_compose() {
  cat >"$INSTALL_DIR/docker-compose.yml" <<'YAML'
services:
  know-me:
    build: ./src
    restart: unless-stopped
    ports:
      - "127.0.0.1:8000:8000"
    env_file:
      - .env
    environment:
      KNOW_ME_CORPUS_ROOT: /data/corpus
      KNOW_ME_PERSONA_DIR: /data/persona
      KNOW_ME_CHAT_SQLITE_PATH: /data/chat.sqlite
    volumes:
      - ./corpus:/data/corpus:ro
      - ./persona:/data/persona:ro
      - ./data:/data
YAML
  ok "已写入 $INSTALL_DIR/docker-compose.yml"
}

write_env() {
  local chat_host="$1" site_host="$2" api_key="$3" chat_model="$4" embed_model="$5"
  local resume_url=""
  if [[ -n "$site_host" ]]; then
    resume_url="https://${site_host}"
  fi
  umask 077
  cat >"$INSTALL_DIR/.env" <<EOF
KNOW_ME_OPENAI_BASE_URL=${KNOW_ME_OPENAI_BASE_URL:-$DASH_BASE_DEFAULT}
KNOW_ME_OPENAI_API_KEY=${api_key}
KNOW_ME_OPENAI_CHAT_MODEL=${chat_model}
KNOW_ME_OPENAI_EMBED_MODEL=${embed_model}
KNOW_ME_RESUME_BROWSER_URL=${resume_url}
KNOW_ME_DISCLAIMER=${KNOW_ME_DISCLAIMER:-本助手基于授权公开资料回答，不构成录用或薪酬承诺。}
EOF
  chmod 600 "$INSTALL_DIR/.env"
  ok "已写入 $INSTALL_DIR/.env（权限 600）"
}

seed_examples_if_empty() {
  if [[ ! -f "$INSTALL_DIR/persona/IDENTITY.md" ]]; then
    warn "persona/ 为空，先复制 persona.example（上线后请换成真实人设）"
    cp -a "$INSTALL_DIR/src/persona.example/." "$INSTALL_DIR/persona/"
  fi
  # corpus.example 有多级目录
  if [[ -z "$(find "$INSTALL_DIR/corpus" -name '*.md' 2>/dev/null | head -1)" ]]; then
    warn "corpus/ 无 md，先复制 corpus.example（上线后请换成真实语料并重建索引）"
    cp -a "$INSTALL_DIR/src/corpus.example/." "$INSTALL_DIR/corpus/"
  fi
}

start_stack() {
  cd "$INSTALL_DIR"
  docker compose build
  docker compose up -d
  ok "容器已启动，等待 health…"
  local i
  for i in $(seq 1 30); do
    if curl -sf http://127.0.0.1:8000/health >/dev/null; then
      ok "health OK"
      return 0
    fi
    sleep 2
  done
  err "health 检查超时，请看: docker compose -f $INSTALL_DIR/docker-compose.yml logs --tail 100"
  exit 1
}

build_index() {
  cd "$INSTALL_DIR"
  warn "构建向量索引（首次可能较慢，依赖 DashScope 嵌入）…"
  docker compose exec -T know-me know-me build-index || {
    err "build-index 失败：检查 API Key / 嵌入模型 id，或稍后手动执行"
    return 1
  }
  ok "索引构建完成"
}

install_nginx() {
  local chat_host="$1"
  if command -v apt-get >/dev/null 2>&1; then
    apt-get install -y nginx
  elif command -v yum >/dev/null 2>&1; then
    yum install -y nginx
  else
    warn "无法自动安装 nginx，请手动配置反代到 127.0.0.1:8000"
    return 0
  fi

  local conf="/etc/nginx/conf.d/know-me.conf"
  cat >"$conf" <<EOF
upstream know_me_upstream {
    server 127.0.0.1:8000;
    keepalive 32;
}

server {
    listen 80;
    server_name ${chat_host};

    location / {
        proxy_pass http://know_me_upstream;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
EOF
  nginx -t
  systemctl enable --now nginx
  systemctl reload nginx
  ok "Nginx HTTP 已配置: http://${chat_host}/"
}

install_certbot() {
  local chat_host="$1"
  if ! command -v apt-get >/dev/null 2>&1; then
    warn "非 apt 系统，请自行申请证书并改 Nginx 为 443"
    return 0
  fi
  apt-get install -y certbot python3-certbot-nginx
  certbot --nginx -d "$chat_host" --non-interactive --agree-tos \
    -m "${KNOW_ME_CERTBOT_EMAIL:-admin@${chat_host}}" \
    --redirect || {
      warn "certbot 失败（检查 DNS 是否已指向本机、80 是否开放）。可稍后手动: certbot --nginx -d ${chat_host}"
      return 0
    }
  ok "HTTPS 已启用: https://${chat_host}/"
}

print_next() {
  local chat_host="$1" site_host="$2"
  cat <<EOF

${GRN}======== Know Me 安装完成 ========${NC}

安装目录: ${INSTALL_DIR}
聊天健康: curl -sf http://127.0.0.1:8000/health
公网（若已配 Nginx）: http://${chat_host}/  或  https://${chat_host}/

${YLW}接下来请你完成：${NC}
1) 用真实语料覆盖 ${INSTALL_DIR}/corpus/ ，真实人设覆盖 ${INSTALL_DIR}/persona/
2) 重建索引:
     cd ${INSTALL_DIR} && docker compose exec know-me know-me build-index
3) 简历站（本机）构建并上传 OSS:
     VITE_CHAT_URL=https://${chat_host} npm run build
     # 详见 docs/deploy-aliyun-checklist.md
4) DNS: ${chat_host} → 本机公网 IP；${site_host:-简历域名} → OSS/CDN
5) 确认 .env 中 KNOW_ME_RESUME_BROWSER_URL=https://${site_host:-你的简历域名}

常用命令:
  cd ${INSTALL_DIR}
  docker compose logs -f
  docker compose restart
  docker compose pull   # 若改用镜像后再说

EOF
}

main() {
  need_root
  log "=== Know Me ECS 安装 ==="

  local KNOW_ME_CHAT_HOST="${KNOW_ME_CHAT_HOST-}"
  local KNOW_ME_SITE_HOST="${KNOW_ME_SITE_HOST-}"
  local KNOW_ME_OPENAI_API_KEY="${KNOW_ME_OPENAI_API_KEY-}"
  local KNOW_ME_OPENAI_CHAT_MODEL="${KNOW_ME_OPENAI_CHAT_MODEL-}"
  local KNOW_ME_OPENAI_EMBED_MODEL="${KNOW_ME_OPENAI_EMBED_MODEL-}"
  local KNOW_ME_INSTALL_NGINX="${KNOW_ME_INSTALL_NGINX-}"
  local KNOW_ME_INSTALL_CERTBOT="${KNOW_ME_INSTALL_CERTBOT-}"

  prompt KNOW_ME_CHAT_HOST "聊天域名 (CHAT_HOST)" "chat.example.com"
  prompt KNOW_ME_SITE_HOST "简历站域名 (SITE_HOST，可稍后改)" "example.com"
  prompt KNOW_ME_OPENAI_API_KEY "DashScope API Key"
  prompt KNOW_ME_OPENAI_CHAT_MODEL "对话模型 id" "qwen-plus"
  prompt KNOW_ME_OPENAI_EMBED_MODEL "嵌入模型 id" "text-embedding-v3"
  prompt KNOW_ME_INSTALL_NGINX "是否安装 Nginx 反代? (1/0)" "1"
  prompt KNOW_ME_INSTALL_CERTBOT "是否用 certbot 申请 HTTPS? (1/0，需 DNS 已指向本机)" "0"

  if [[ -z "$KNOW_ME_OPENAI_API_KEY" ]]; then
    err "API Key 不能为空"
    exit 1
  fi

  install_docker
  clone_or_update
  write_compose
  write_env "$KNOW_ME_CHAT_HOST" "$KNOW_ME_SITE_HOST" "$KNOW_ME_OPENAI_API_KEY" \
    "$KNOW_ME_OPENAI_CHAT_MODEL" "$KNOW_ME_OPENAI_EMBED_MODEL"
  seed_examples_if_empty
  start_stack
  build_index || true

  if [[ "$KNOW_ME_INSTALL_NGINX" == "1" ]]; then
    install_nginx "$KNOW_ME_CHAT_HOST"
    if [[ "$KNOW_ME_INSTALL_CERTBOT" == "1" ]]; then
      install_certbot "$KNOW_ME_CHAT_HOST"
    fi
  fi

  print_next "$KNOW_ME_CHAT_HOST" "$KNOW_ME_SITE_HOST"
}

main "$@"
