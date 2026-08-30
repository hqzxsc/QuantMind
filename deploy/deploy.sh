#!/usr/bin/env bash
# QuantMind 在线一键部署（Ubuntu 22.04 / 24.04）
# 用法：sudo bash deploy/deploy.sh [--ref master] [--force]

set -Eeuo pipefail

PROJECT_DIR="${QUANTMIND_PROJECT_DIR:-/opt/quantmind}"
REPO_URL="${QUANTMIND_REPO_URL:-https://gitee.com/qusong0627/QuantMind.git}"
REF="${QUANTMIND_REF:-master}"
DOCKER_MIRROR="${QUANTMIND_DOCKER_MIRROR:-https://fx07btib0z92d2dhxl.xuanyuan.run}"
FORCE=false

log() { printf '[quantmind-deploy] %s\n' "$*"; }
die() { log "错误: $*" >&2; exit 1; }

usage() {
    cat <<'EOF'
用法: sudo bash deploy/deploy.sh [选项]

  --ref <branch|tag>  部署代码版本（默认 master）
  --force             覆盖部署目录中未提交的代码改动，不删除业务数据
  -h, --help          显示帮助
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --ref) REF="${2:-}"; shift 2 ;;
        --force) FORCE=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

require_root() { [[ $EUID -eq 0 ]] || die '请使用 sudo 执行'; }
require_ubuntu() {
    . /etc/os-release
    [[ ${ID:-} == ubuntu ]] || die '仅支持 Ubuntu'
}

configure_docker_mirror() {
    DOCKER_MIRROR="$DOCKER_MIRROR" python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path('/etc/docker/daemon.json')
try:
    config = json.loads(path.read_text()) if path.exists() else {}
except json.JSONDecodeError as exc:
    raise SystemExit(f'Docker 配置文件格式错误: {exc}')

mirror = os.environ['DOCKER_MIRROR']
config['registry-mirrors'] = [mirror] + [
    item for item in config.get('registry-mirrors', []) if item != mirror
]
temporary = path.with_suffix('.json.quantmind-tmp')
path.parent.mkdir(parents=True, exist_ok=True)
temporary.write_text(json.dumps(config, indent=2) + '\n')
temporary.replace(path)
PY
}

install_runtime() {
    log '1/5 安装系统依赖、Docker 与 Compose'
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        ca-certificates curl git python3 openssl zstd docker.io
    if ! docker compose version >/dev/null 2>&1; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-plugin 2>/dev/null \
            || DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-v2
    fi
    configure_docker_mirror
    systemctl enable docker
    systemctl restart docker
    docker compose version >/dev/null || die 'Docker Compose 不可用'
}

sync_code() {
    log "2/5 同步代码：$REF"
    if [[ -e "$PROJECT_DIR" && ! -d "$PROJECT_DIR/.git" ]]; then
        die "部署目录不是 Git 仓库: $PROJECT_DIR"
    fi
    if [[ ! -d "$PROJECT_DIR/.git" ]]; then
        git clone --depth 1 --branch "$REF" "$REPO_URL" "$PROJECT_DIR"
        return
    fi

    if ! git -C "$PROJECT_DIR" diff --quiet || ! git -C "$PROJECT_DIR" diff --cached --quiet; then
        $FORCE || die '检测到未提交代码改动；确认覆盖请加 --force'
    fi
    git -C "$PROJECT_DIR" fetch origin "$REF"
    git -C "$PROJECT_DIR" checkout -B "$REF" "origin/$REF"
}

ensure_env() {
    local env_file="$PROJECT_DIR/.env"
    [[ -f "$env_file" ]] && return
    log '3/5 生成 .env'
    umask 077
    cat > "$env_file" <<EOF
DB_PASSWORD=$(openssl rand -hex 24)
SECRET_KEY=$(openssl rand -hex 32)
JWT_SECRET_KEY=$(openssl rand -hex 32)
STORAGE_MODE=local
EOF
}

start_services() {
    log '4/5 构建并启动服务'
    cd "$PROJECT_DIR"
    docker compose pull || log '部分镜像未能预拉取，将在构建或启动时重试'
    docker compose build quantmind
    docker compose up -d --remove-orphans
}

health_check() {
    log '5/5 检查核心服务'
    local attempt
    for attempt in {1..30}; do
        if curl --fail --silent --max-time 3 http://127.0.0.1:8000/health >/dev/null; then
            docker compose -f "$PROJECT_DIR/docker-compose.yml" ps
            log "部署完成：$PROJECT_DIR"
            return
        fi
        sleep 2
    done
    docker compose -f "$PROJECT_DIR/docker-compose.yml" ps || true
    die '服务未在 60 秒内通过健康检查，请查看 docker compose logs quantmind'
}

show_completion_tips() {
    echo ""
    echo "========================================================================="
    echo " 🎉 QuantMind 服务部署完成！"
    echo " -------------------------------------------------------------------------"
    echo " 🌐 Web 控制台  : http://<服务器 IP>:3000"
    echo " 📖 API 文档    : http://<服务器 IP>:8000/docs"
    echo " 👤 默认账号    : admin / admin123"
    echo " -------------------------------------------------------------------------"
    echo " 💡 【重要：数据准备与更新提示】"
    echo " 系统正常运行需基础量化数据，请选择以下任一方式准备数据："
    echo " 1. QuantDB 在线下载及更新（推荐）："
    echo "    在 Web 端【个人中心】->【数据平台】中填入 API Key 即可在线同步，"
    echo "    或在终端执行: docker exec quantmind python backend/scripts/quantdb_daily_sync.py"
    echo " 2. 百度网盘离线数据包（备选）："
    echo "    链接: https://pan.baidu.com/s/5IT4p5nFlglZ7zu_0H_fA8Q"
    echo "    下载后解压覆盖到 $PROJECT_DIR/data 与 $PROJECT_DIR/db/qlib_data"
    echo "========================================================================="
    echo ""
}

main() {
    require_root
    require_ubuntu
    install_runtime
    sync_code
    ensure_env
    start_services
    health_check
    show_completion_tips
}

main
