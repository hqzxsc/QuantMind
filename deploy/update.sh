#!/usr/bin/env bash
# QuantMind 一键更新脚本
# 流程：同步最新代码 → 重建核心镜像 → 重启服务 → 导入数据库补丁(data/upgrade_*.sql) → 健康检查。
# 用法：sudo bash deploy/update.sh [--ref master] [--force] [--no-build]

set -Eeuo pipefail

PROJECT_DIR="${QUANTMIND_PROJECT_DIR:-/opt/quantmind}"
REF="${QUANTMIND_REF:-master}"
REMOTE="${QUANTMIND_REMOTE:-origin}"   # 支持多远端（gitee/github/origin）
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
  --no-build            跳过核心镜像构建，仅重启服务
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

# 升级前数据库快照（pg_dump 压缩到 data/backups/，便于回滚）
backup_database() {
    if $SKIP_BACKUP; then
        log '跳过升级前数据库备份（--skip-backup）'
        return
    fi
    log '0/6 升级前数据库备份'
    local backup_dir="$PROJECT_DIR/data/backups"
    mkdir -p "$backup_dir"
    local stamp
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    local backup_file="$backup_dir/quantmind_pre_update_${stamp}.sql.gz"

    if ! docker ps --format '{{.Names}}' | grep -qx 'quantmind-db'; then
        log '  quantmind-db 未运行，跳过备份（首次部署场景）'
        return
    fi

    # 取 POSTGRES_USER/POSTGRES_DB：优先 .env，回退 docker-compose.yml 默认值
    local pg_user pg_db
    pg_user="$(grep -E '^DB_USER=' "$PROJECT_DIR/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d \"\' || true)"
    pg_db="$(grep -E '^DB_NAME=' "$PROJECT_DIR/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d \"\' || true)"
    pg_user="${pg_user:-quantmind}"
    pg_db="${pg_db:-quantmind}"

    # pg_dump 失败不阻断升级（备份是防御性，非关键路径），但要给用户明确日志
    if docker exec -e PGPASSWORD="${DB_PASSWORD:-quantmind2026}" quantmind-db \
            pg_dump -U "$pg_user" -d "$pg_db" --no-owner --no-acl --clean --if-exists 2>/dev/null \
            | gzip > "$backup_file"; then
        log "  已备份: $backup_file ($(du -h "$backup_file" | cut -f1))"
    else
        log "  备份失败（不影响升级），跳过"
        rm -f "$backup_file"
    fi
}

sync_code() {
    log "1/6 同步代码：$REF (remote=$REMOTE)"
    if ! git -C "$PROJECT_DIR" diff --quiet || ! git -C "$PROJECT_DIR" diff --cached --quiet; then
        $FORCE || die '检测到未提交代码改动；确认覆盖请加 --force'
        # 仅重置被跟踪文件的改动；不做 git clean，避免误删未跟踪的运行资产
        # （.env、logs/、user_pools_local/、data/ 业务数据 等）。
        git -C "$PROJECT_DIR" reset --hard
        git -C "$PROJECT_DIR" clean -fd -e data -e models -e db -e logs -e user_pools_local -e .env
    fi

    # 尝试拉取指定远端；若远端不存在（如项目实际只有 gitee/github），回退到任意远端的同名 ref，
    # 最后回退到本地 tag。这样不会因为 origin 不存在而静默"升级"到旧版。
    if git -C "$PROJECT_DIR" remote get-url "$REMOTE" >/dev/null 2>&1; then
        git -C "$PROJECT_DIR" fetch "$REMOTE" "$REF" || die "git fetch $REMOTE $REF 失败"
        local fetched_ref="FETCH_HEAD"
    else
        log "  远端 $REMOTE 不存在，回退到所有远端"
        if ! git -C "$PROJECT_DIR" fetch --all "$REF" 2>/dev/null; then
            die "git fetch 失败，请检查网络或远端配置"
        fi
        # 从任一远端找匹配的 ref
        local found
        found="$(git -C "$PROJECT_DIR" for-each-ref --format='%(refname)' \
                    "refs/remotes/*/$REF" | head -1 || true)"
        if [[ -z "$found" ]]; then
            log "  未在远端找到 $REF，回退到本地 ref"
            local fetched_ref="$REF"
        else
            local fetched_ref="$found"
        fi
    fi

    # 分支走远端 ref；tag/无 remote-tracking 回退到本地 ref（detached）
    git -C "$PROJECT_DIR" checkout -B "$REF" "$fetched_ref" 2>/dev/null \
        || git -C "$PROJECT_DIR" checkout --detach "$fetched_ref" \
        || die "checkout $REF 失败"

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
        log '2/6 跳过镜像构建'
        return
    fi
    log '2/6 重建核心后端镜像'
    docker compose -f "$PROJECT_DIR/docker-compose.yml" build quantmind
}

# 等待 db 容器 healthy（pg_isready 多次重试），避免 SQL 升级在 db 还没就绪时撞崩溃
wait_db_ready() {
    local max="${1:-30}"   # 默认 30 × 2s = 60s
    local attempt
    local pg_user
    pg_user="$(grep -E '^DB_USER=' "$PROJECT_DIR/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d \"\' || echo quantmind)"

    # 先确认 db 容器存在且在跑
    if ! docker ps --format '{{.Names}}' | grep -qx 'quantmind-db'; then
        # 不存在则尝试启动 db（按 compose 文件定义，仅起 db + 它的依赖 redis）
        log "  启动 quantmind-db 容器"
        docker compose -f "$PROJECT_DIR/docker-compose.yml" up -d --no-deps db \
            || die "启动 quantmind-db 失败，请手动检查"
    fi

    log "  等待 quantmind-db 就绪（pg_isready，user=$pg_user）"
    for attempt in $(seq 1 "$max"); do
        if docker exec -e PGUSER="$pg_user" quantmind-db \
                pg_isready -U "$pg_user" -d "${DB_NAME:-quantmind}" >/dev/null 2>&1; then
            log "  db 就绪（尝试 $attempt/$max）"
            return 0
        fi
        # 进程在但 unhealthy 时打印一句进度
        if (( attempt % 5 == 0 )); then
            local status
            status="$(docker inspect --format '{{.State.Status}}  health={{if .State.Health}}{{.State.Health.Status}}{{end}}' quantmind-db 2>/dev/null || echo unknown)"
            log "  等待中（$attempt/$max，状态: $status）"
        fi
        sleep 2
    done

    # 超时：把日志丢给用户，提示排查
    log '  db 未在预期时间内就绪，打印尾部日志协助排查：' >&2
    docker logs --tail 100 quantmind-db >&2 || true
    die 'quantmind-db 启动超时，请检查上述日志（多半是数据卷权限/PG 版本不兼容/.env 与容器实际配置不一致）'
}

restart_services() {
    log '3/6 重启核心服务（不动 db 容器，由 restart: unless-stopped 兜底）'
    cd "$PROJECT_DIR"
    # 关键：只重启 application 层容器，**不**碰 db/redis/qwenpaw 等基础设施。
    # 用 --no-deps --force-recreate 精确控制；不用 `up -d --remove-orphans`，
    # 否则会拉起整个栈（包括本地 build 的 data-gateway/dashboard/qwenpaw，若镜像未 build 会失败），
    # 并可能因 docker-compose.yml 漂移导致 db 被重建 → 跨大版本数据卷不兼容。
    local services=(quantmind)
    local service
    for service in celery-worker celery-beat; do
        if docker compose config --services | grep -qx "$service"; then
            services+=("$service")
        fi
    done
    docker compose up -d --no-deps --force-recreate "${services[@]}"
}

update_database() {
    log '4/6 导入数据库升级补丁'
    # 应用 data/upgrade_*.sql（v1.0.1、v1.0.2 ……）。复用 full-deploy 的
    # 同款 docker exec 方式走 db 容器 psql，SQL 需保持幂等（可重复执行）。
    # 先确保 db 真正 healthy 再执行 psql，避免 db 还没起来就 exec 失败。
    wait_db_ready 30

    # 取 POSTGRES_USER
    local pg_user
    pg_user="$(grep -E '^DB_USER=' "$PROJECT_DIR/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d \"\' || echo quantmind)"

    local patch applied
    applied=0
    for patch in "$PROJECT_DIR"/data/upgrade_*.sql; do
        [[ -e "$patch" ]] || continue
        log "  应用: $(basename "$patch")"
        if ! docker exec -i -e PGUSER="$pg_user" quantmind-db \
                sh -lc 'psql -U "$PGUSER" -v ON_ERROR_STOP=1' < "$patch"; then
            die "数据库升级补丁执行失败: $(basename "$patch")"
        fi
        log "  ✓ 已应用: $(basename "$patch")"
        applied=$((applied + 1))
    done
    if (( applied == 0 )); then
        log '  未发现数据库升级补丁，跳过'
    fi
}

health_check() {
    log '5/6 检查健康状态'
    local attempt
    for attempt in {1..30}; do
        if curl --fail --silent --max-time 3 http://127.0.0.1:8000/health >/dev/null; then
            log '  ✓ API health OK'
            docker compose -f "$PROJECT_DIR/docker-compose.yml" ps
            return
        fi
        # 失败时若 db unhealthy，把日志一起抛出来便于排错
        if (( attempt == 10 || attempt == 20 )); then
            local db_state
            db_state="$(docker inspect --format '{{.State.Health.Status}}' quantmind-db 2>/dev/null || echo unknown)"
            if [[ "$db_state" != "healthy" ]]; then
                log "  ⚠ db 健康状态: $db_state（将一并打印尾部日志）"
                docker logs --tail 50 quantmind-db >&2 || true
            fi
        fi
        sleep 2
    done
    log '  失败：打印关键容器尾部日志便于排查' >&2
    docker logs --tail 100 quantmind >&2 || true
    docker logs --tail 50 quantmind-db >&2 || true
    die '健康检查失败（30 × 2s 内 API 未 ready）'
}

# 升级完成总结
summary() {
    log '6/6 升级完成'
    local head_sha head_describe
    head_sha="$(git -C "$PROJECT_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    head_describe="$(git -C "$PROJECT_DIR" describe --tags --always 2>/dev/null || echo dev)"
    log "  当前版本: $head_describe ($head_sha)"
    log "  远端分支: $REMOTE/$REF"
    log "  数据库备份: $PROJECT_DIR/data/backups/quantmind_pre_update_*.sql.gz（如未跳过）"
    log "  若需回滚代码: cd $PROJECT_DIR && git reset --hard <旧SHA>"
    log "  若需回滚数据: gunzip < backup && docker exec -i quantmind-db psql -U $DB_USER -d $DB_NAME"
}

main() {
    require_root
    require_project
    backup_database
    sync_code
    build_core
    restart_services
    update_database
    health_check
    summary
}

main
