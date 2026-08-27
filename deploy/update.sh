#!/usr/bin/env bash
# QuantMind 一键更新脚本
# 用法：sudo bash deploy/update.sh [--ref master] [--force] [--no-build]

set -Eeuo pipefail

PROJECT_DIR="${QUANTMIND_PROJECT_DIR:-/opt/quantmind}"
REF="${QUANTMIND_REF:-master}"
FORCE=false
BUILD=true

log() { printf '[quantmind-update] %s\n' "$*"; }
die() { log "错误: $*" >&2; exit 1; }

usage() {
    cat <<'EOF'
用法: sudo bash deploy/update.sh [选项]

  --ref <branch|tag>  更新到指定版本（默认 master）
  --force             覆盖服务器上的未提交代码改动，不删除业务数据
  --no-build          跳过核心镜像构建，仅重启服务
  -h, --help          显示帮助
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --ref) REF="${2:-}"; shift 2 ;;
        --force) FORCE=true; shift ;;
        --no-build) BUILD=false; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

require_root() { [[ $EUID -eq 0 ]] || die '请使用 sudo 执行'; }
require_project() {
    [[ -d "$PROJECT_DIR/.git" ]] || die "不是 Git 部署目录: $PROJECT_DIR"
    [[ -f "$PROJECT_DIR/docker-compose.yml" ]] || die "缺少 docker-compose.yml: $PROJECT_DIR"
    command -v docker >/dev/null || die 'Docker 未安装'
    docker compose version >/dev/null || die 'Docker Compose 不可用'
}

sync_code() {
    log "1/5 同步代码：$REF"
    if ! git -C "$PROJECT_DIR" diff --quiet || ! git -C "$PROJECT_DIR" diff --cached --quiet; then
        $FORCE || die '检测到未提交代码改动；确认覆盖请加 --force'
        git -C "$PROJECT_DIR" reset --hard
        git -C "$PROJECT_DIR" clean -fd -e data -e models -e db
    fi
    git -C "$PROJECT_DIR" fetch origin "$REF"
    git -C "$PROJECT_DIR" checkout -B "$REF" "origin/$REF"
}

prepare_models() {
    log '2/5 准备离线模型（FinBERT 新闻情感）'
    # 缺失时系统会静默降级为纯词典法情感，所以这里尽力下载但不阻断更新
    if command -v python3 >/dev/null; then
        python3 "$PROJECT_DIR/backend/scripts/download_finbert.py" && return 0
        log '宿主机 python3 下载失败，改用容器内 python 重试'
    fi
    docker run --rm         -v "$PROJECT_DIR/models:/app/models"         -v "$PROJECT_DIR/backend:/app/backend:ro"         "$(docker compose -f "$PROJECT_DIR/docker-compose.yml" config --image quantmind)"         python /app/backend/scripts/download_finbert.py         || log '⚠️ FinBERT 模型下载失败（网络原因），新闻情感将暂用词典法，可重跑 update.sh 补齐'
}

build_core() {
    if ! $BUILD; then
        log '3/5 跳过镜像构建'
        return
    fi
    log '3/5 重建核心后端镜像'
    docker compose -f "$PROJECT_DIR/docker-compose.yml" build quantmind
}

restart_services() {
    log '4/5 重启核心服务'
    cd "$PROJECT_DIR"
    local services=(quantmind)
    local service
    for service in celery-worker celery-beat; do
        if docker compose config --services | grep -qx "$service"; then
            services+=("$service")
        fi
    done
    docker compose up -d --no-deps --force-recreate "${services[@]}"
    docker compose up -d --remove-orphans
}

health_check() {
    log '5/5 检查健康状态'
    local attempt
    for attempt in {1..30}; do
        if curl --fail --silent --max-time 3 http://127.0.0.1:8000/health >/dev/null; then
            docker compose -f "$PROJECT_DIR/docker-compose.yml" ps
            log '更新完成'
            return
        fi
        sleep 2
    done
    docker compose -f "$PROJECT_DIR/docker-compose.yml" ps || true
    die '健康检查失败，请查看 docker compose logs quantmind'
}

main() {
    require_root
    require_project
    sync_code
    prepare_models
    build_core
    restart_services
    health_check
}

main
