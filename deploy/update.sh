#!/usr/bin/env bash
# QuantMind 一键更新脚本
# 流程：同步最新代码 → 重建核心镜像 → 重启服务 → 导入数据库补丁(data/upgrade_*.sql) → 健康检查。
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
        # 仅重置被跟踪文件的改动；不做 git clean，避免误删未跟踪的运行资产
        # （.env、logs/、user_pools_local/、data/ 业务数据 等）。
        git -C "$PROJECT_DIR" reset --hard
        git -C "$PROJECT_DIR" clean -fd -e data -e models -e db -e logs -e user_pools_local -e .env
    fi
    git -C "$PROJECT_DIR" fetch origin "$REF"
    # 分支走 origin/$REF；tag 无 remote-tracking ref，回退到本地 tag（detached）。
    git -C "$PROJECT_DIR" checkout -B "$REF" "origin/$REF" 2>/dev/null \
        || git -C "$PROJECT_DIR" checkout --detach "$REF"

    # 写入代码版本供 version.py 读取（git describe --tags --always 格式）。
    # 文件在 .gitignore 内，不入仓库；describe 失败时删掉旧文件，避免回退 dev 时残留过期版本。
    git -C "$PROJECT_DIR" describe --tags --always \
        > "$PROJECT_DIR/backend/shared/version.txt" 2>/dev/null \
        || rm -f "$PROJECT_DIR/backend/shared/version.txt"

    # 同时写入 version.json：含完整 HEAD SHA 与部署分支，供后端运行时查上游更新
    # （compare API 需完整 SHA；describe 只在恰为 tag 时可能缺短 SHA，故这里必须取 rev-parse）。
    # 同样在 .gitignore 内、随 build_core 的 COPY backend 一并拷入镜像。
    local head_sha head_describe
    head_sha="$(git -C "$PROJECT_DIR" rev-parse HEAD 2>/dev/null || true)"
    head_describe="$(git -C "$PROJECT_DIR" describe --tags --always 2>/dev/null || echo dev)"
    if [[ -n "$head_sha" ]]; then
        cat > "$PROJECT_DIR/backend/shared/version.json" <<EOF
{
  "version": "$head_describe",
  "commit": "$head_sha",
  "branch": "$REF",
  "generated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
    else
        rm -f "$PROJECT_DIR/backend/shared/version.json"
    fi
}

build_core() {
    if ! $BUILD; then
        log '2/5 跳过镜像构建'
        return
    fi
    log '2/5 重建核心后端镜像'
    docker compose -f "$PROJECT_DIR/docker-compose.yml" build quantmind
}

restart_services() {
    log '3/5 重启核心服务'
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

update_database() {
    log '4/5 导入数据库升级补丁'
    # 应用 data/upgrade_*.sql（v1.0.1、v1.0.2 ……）。复用 full-deploy 的
    # 同款 docker exec 方式走 db 容器 psql，SQL 需保持幂等（可重复执行）。
    local patch applied
    applied=0
    for patch in "$PROJECT_DIR"/data/upgrade_*.sql; do
        [[ -e "$patch" ]] || continue
        docker exec -i quantmind-db sh -lc 'psql -U "$POSTGRES_USER"' < "$patch" \
            || die "数据库升级补丁执行失败: $(basename "$patch")"
        log "已应用数据库补丁: $(basename "$patch")"
        applied=$((applied + 1))
    done
    if (( applied == 0 )); then
        log '未发现数据库升级补丁，跳过'
    fi
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
    build_core
    restart_services
    update_database
    health_check
}

main
