#!/usr/bin/env bash
# QuantMind 一键更新脚本
# 核心流程：拉代码 → 重建/重启后端容器 → 跑 data/upgrade_*.sql → 健康检查。
# db/redis/qwenpaw 等基础设施容器不动（restart: unless-stopped 兜底）。
# 用法：sudo bash deploy/update.sh [--ref master] [--remote gitee|github|origin] [--force] [--no-build] [--skip-backup]

set -Eeuo pipefail

PROJECT_DIR="${QUANTMIND_PROJECT_DIR:-/opt/quantmind}"
REF="${QUANTMIND_REF:-master}"
REMOTE="${QUANTMIND_REMOTE:-origin}"   # 项目实际远端是 gitee/github；默认 origin 兼容旧配置
FORCE=false
BUILD=true
SKIP_BACKUP=false

log() { printf '[quantmind-update] %s\n' "$*"; }
die() { log "错误: $*" >&2; exit 1; }

usage() {
    cat <<'EOF'
用法: sudo bash deploy/update.sh [选项]

  --ref <branch|tag>    更新到指定版本（默认 master）
  --remote <name>       远端名（默认 origin；项目实际远端是 gitee/github）
  --force               覆盖服务器上的未提交代码改动，不删除业务数据
  --no-build            跳过核心镜像构建（仅代码改动时用，bind mount 已生效）
  --skip-backup         跳过升级前数据库备份
  -h, --help            显示帮助
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --ref) REF="${2:-}"; shift 2 ;;
        --remote) REMOTE="${2:-}"; shift 2 ;;
        --force) FORCE=true; shift ;;
        --no-build) BUILD=false; shift ;;
        --skip-backup) SKIP_BACKUP=true; shift ;;
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

# 升级前数据库快照（防御性：失败不阻断升级；可 --skip-backup 跳过）
backup_database() {
    if $SKIP_BACKUP; then
        log '跳过升级前数据库备份（--skip-backup）'
        return
    fi
    if ! docker ps --format '{{.Names}}' | grep -qx 'quantmind-db'; then
        log 'quantmind-db 未运行，跳过备份'
        return
    fi
    local backup_dir="$PROJECT_DIR/data/backups"
    mkdir -p "$backup_dir"
    local stamp backup_file pg_user pg_db
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    backup_file="$backup_dir/quantmind_pre_update_${stamp}.sql.gz"
    pg_user="$(grep -E '^DB_USER=' "$PROJECT_DIR/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d \"\' || echo quantmind)"
    pg_db="$(grep -E '^DB_NAME=' "$PROJECT_DIR/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d \"\' || echo quantmind)"
    if docker exec -e PGPASSWORD="${DB_PASSWORD:-quantmind2026}" quantmind-db \
            pg_dump -U "$pg_user" -d "$pg_db" --no-owner --no-acl --clean --if-exists 2>/dev/null \
            | gzip > "$backup_file"; then
        log "数据库备份完成: $backup_file ($(du -h "$backup_file" | cut -f1))"
    else
        log "数据库备份失败（不影响升级）"
        rm -f "$backup_file"
    fi
}

sync_code() {
    log "1/4 同步代码：$REMOTE/$REF"
    if ! git -C "$PROJECT_DIR" diff --quiet || ! git -C "$PROJECT_DIR" diff --cached --quiet; then
        $FORCE || die '检测到未提交代码改动；确认覆盖请加 --force'
        git -C "$PROJECT_DIR" reset --hard
        git -C "$PROJECT_DIR" clean -fd -e data -e models -e db -e logs -e user_pools_local -e .env
    fi

    # 拉远端：指定远端不存在时回退 fetch --all（项目实际是 gitee/github）
    if git -C "$PROJECT_DIR" remote get-url "$REMOTE" >/dev/null 2>&1; then
        git -C "$PROJECT_DIR" fetch "$REMOTE" "$REF" || die "git fetch $REMOTE $REF 失败"
        local fetched_ref="FETCH_HEAD"
    else
        log "  远端 $REMOTE 不存在，回退 fetch --all"
        git -C "$PROJECT_DIR" fetch --all "$REF" 2>/dev/null || die "git fetch 失败"
        local fetched_ref
        fetched_ref="$(git -C "$PROJECT_DIR" for-each-ref --format='%(refname)' \
                        "refs/remotes/*/$REF" | head -1 || true)"
        fetched_ref="${fetched_ref:-$REF}"
    fi

    git -C "$PROJECT_DIR" checkout -B "$REF" "$fetched_ref" 2>/dev/null \
        || git -C "$PROJECT_DIR" checkout --detach "$fetched_ref" \
        || die "checkout $REF 失败"

    # 写入版本号到 backend/shared/version.{txt,json}（.gitignore 内）
    git -C "$PROJECT_DIR" describe --tags --always \
        > "$PROJECT_DIR/backend/shared/version.txt" 2>/dev/null \
        || rm -f "$PROJECT_DIR/backend/shared/version.txt"

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
    # 智能判断：只有"会改变镜像层"的文件变更才触发 build
    #   - requirements*.txt：新增/升级 pip 依赖
    #   - docker/Dockerfile* 或 .build-args：构建参数/基础镜像变更
    #   - 其他（backend/, config/, scripts/ 等）都是 bind mount，**不**需要重 build
    # 这样 95% 的纯代码升级从 5min 缩到 30s
    if ! $BUILD; then
        log '2/4 跳过镜像构建（--no-build 显式指定）'
        return
    fi
    local changes
    changes="$(git -C "$PROJECT_DIR" diff --name-only HEAD@{1} HEAD 2>/dev/null \
        | grep -E '^(requirements.*\.txt|requirements/.*\.txt|docker/Dockerfile.*|docker/.*\.build-args)$' \
        || true)"
    if [[ -z "$changes" ]]; then
        log "2/4 跳过镜像构建（本次无 requirements/Dockerfile 变更；后端代码 bind mount 已生效）"
        return
    fi
    log "2/4 重建核心后端镜像（检测到依赖/构建参数变更：$(echo "$changes" | tr '\n' ' ' | head -c 200)）"
    docker compose -f "$PROJECT_DIR/docker-compose.yml" build quantmind
}

# 关键步骤：只重启 application 层容器，**不**碰 db/redis/qwenpaw 等基础设施
# （db 已 restart: unless-stopped，无需脚本干预；碰它才容易翻车）
restart_services() {
    log '3/4 重启后端服务（quantmind + celery；不动 db/redis/qwenpaw）'
    cd "$PROJECT_DIR"
    local services=(quantmind)
    local service
    for service in celery-worker celery-beat; do
        if docker compose config --services | grep -qx "$service"; then
            services+=("$service")
        fi
    done
    docker compose up -d --no-deps --force-recreate "${services[@]}"
}

# 跑 data/upgrade_*.sql —— 这是用户最关心的"执行 SQL"主流程。
# db 健康检查：仅短等待（5×2s=10s 足够初始启动），不健康立即报错而不是傻等。
update_database() {
    log '4/4 执行数据库升级 SQL (data/upgrade_*.sql)'
    local max_attempts=5
    local attempt
    local pg_user
    pg_user="$(grep -E '^DB_USER=' "$PROJECT_DIR/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d \"\' || echo quantmind)"

    # 确认 db 容器在跑
    if ! docker ps --format '{{.Names}}' | grep -qx 'quantmind-db'; then
        log '  启动 quantmind-db'
        docker compose -f "$PROJECT_DIR/docker-compose.yml" up -d --no-deps db \
            || die "启动 quantmind-db 失败"
    fi

    # 短等待 db 接受连接（pg_isready 不阻塞，最多 5×2s=10s）
    for attempt in $(seq 1 "$max_attempts"); do
        if docker exec -e PGUSER="$pg_user" quantmind-db \
                pg_isready -U "$pg_user" >/dev/null 2>&1; then
            break
        fi
        if (( attempt == max_attempts )); then
            log '  quantmind-db 不可达，打印尾部日志：' >&2
            docker logs --tail 50 quantmind-db >&2 || true
            die 'quantmind-db 不可用，请先修复 db 容器（多数情况是数据卷权限/PG 版本/.env 不一致）'
        fi
        sleep 2
    done

    # 应用 SQL 补丁
    local patch applied
    applied=0
    for patch in "$PROJECT_DIR"/data/upgrade_*.sql; do
        [[ -e "$patch" ]] || continue
        log "  应用 SQL: $(basename "$patch")"
        if ! docker exec -i -e PGUSER="$pg_user" quantmind-db \
                sh -lc 'psql -U "$PGUSER" -v ON_ERROR_STOP=1' < "$patch"; then
            die "SQL 升级失败: $(basename "$patch")"
        fi
        log "  ✓ $(basename "$patch") 已应用"
        applied=$((applied + 1))
    done
    if (( applied == 0 )); then
        log '  未发现 data/upgrade_*.sql，跳过'
    fi
}

main() {
    require_root
    require_project
    backup_database
    sync_code
    build_core
    restart_services
    update_database

    # 健康检查：API 起来即视为升级成功
    local attempt
    for attempt in {1..30}; do
        if curl --fail --silent --max-time 3 http://127.0.0.1:8000/health >/dev/null; then
            log "升级完成 ✓ (HEAD: $(git -C "$PROJECT_DIR" rev-parse --short HEAD))"
            return
        fi
        sleep 2
    done
    log '健康检查失败，尾部日志：' >&2
    docker logs --tail 100 quantmind >&2 || true
    if [[ "$(docker inspect --format '{{.State.Health.Status}}' quantmind-db 2>/dev/null)" != "healthy" ]]; then
        docker logs --tail 50 quantmind-db >&2 || true
    fi
    die 'API 未在 60s 内 ready，请根据上述日志排查'
}

main
