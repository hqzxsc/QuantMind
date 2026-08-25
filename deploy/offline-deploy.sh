#!/usr/bin/env bash
# QuantMind 离线镜像一键部署
#
# 默认 CDN 地址：
#   https://www.quantmindai.cn/downloads
# 可用 QUANTMIND_OFFLINE_BASE_URL 覆盖默认地址。
# 可选环境变量：
#   QUANTMIND_MANIFEST_SHA256  SHA256SUMS 清单的 SHA-256（可选）
#   QUANTMIND_DOCKER_MIRROR  Docker 镜像加速地址
#   QUANTMIND_REPO_URL    代码仓库地址（默认 Gitee）
#   QUANTMIND_REF         要部署的 Git 分支或 tag（默认 master）
#   QUANTMIND_REPLACE_QLIB=true       覆盖已有 db/qlib_data（谨慎）
#   QUANTMIND_REPLACE_DATABASE=true   覆盖已有 PostgreSQL 业务数据（谨慎）
#   QUANTMIND_REPLACE_QWENPAW_DATA=true 覆盖已有 QwenPaw 持久化数据（谨慎）
#   QUANTMIND_COMPOSE_OVERLAY  已验证 docker-compose.yml 的本地路径（可选）
#   QUANTMIND_DEPLOY_OVERLAY_DIR  受控 Dockerfile 覆盖目录（可选）

set -euo pipefail

PROJECT_DIR="${QUANTMIND_PROJECT_DIR:-/opt/quantmind}"
DOWNLOAD_DIR="${QUANTMIND_DOWNLOAD_DIR:-/opt/quantmind-downloads}"
STAGING_DIR="${QUANTMIND_STAGING_DIR:-/opt/quantmind-staging}"
REPO_URL="${QUANTMIND_REPO_URL:-https://gitee.com/qusong0627/QuantMind.git}"
REF="${QUANTMIND_REF:-master}"
COMPOSE_OVERLAY="${QUANTMIND_COMPOSE_OVERLAY:-}"
DEPLOY_OVERLAY_DIR="${QUANTMIND_DEPLOY_OVERLAY_DIR:-}"
OFFLINE_BASE_URL="${QUANTMIND_OFFLINE_BASE_URL:-https://www.quantmindai.cn/downloads}"
OFFLINE_BASE_URL="${OFFLINE_BASE_URL%/}"
MANIFEST_SHA256="${QUANTMIND_MANIFEST_SHA256:-}"
DOCKER_MIRROR="${QUANTMIND_DOCKER_MIRROR:-https://gpu34ekhgwm14ghgur.xuanyuan.run}"
PACKAGE_DIR="$DOWNLOAD_DIR/quantmind-offline"

log() { printf '[offline-deploy] %s\n' "$*"; }
die() { log "错误: $*" >&2; exit 1; }

require_root() { [[ ${EUID} -eq 0 ]] || die '请使用 sudo bash deploy/offline-deploy.sh'; }
require_ubuntu() {
    . /etc/os-release
    [[ ${ID:-} == ubuntu ]] || die '仅支持 Ubuntu'
}
require_url() { [[ -n "$1" ]] || die "缺少环境变量 $2"; }

download() {
    local url="$1" destination="$2" expected_sha="${3:-}"
    mkdir -p "$(dirname "$destination")"

    # 已下载且校验通过的包直接复用，避免重跑时 curl -C - 对完整文件
    # 返回 HTTP 416，也避免重复下载数 GB 的镜像包。
    if [[ -n "$expected_sha" && -f "$destination" ]] \
        && echo "${expected_sha}  ${destination}" | sha256sum --check --status; then
        log "复用已校验下载包: $(basename "$destination")"
        return 0
    fi
    log "下载 $(basename "$destination")"
    curl --fail --location --continue-at - --retry 3 --retry-delay 3 \
        "$url" -o "$destination"
    [[ -s "$destination" ]] || die "下载结果为空: $destination"
    if [[ -n "$expected_sha" ]]; then
        echo "${expected_sha}  ${destination}" | sha256sum --check --status \
            || die "SHA-256 校验失败: $destination"
    fi
}

download_offline_package() {
    log '步骤 3/8：从 CDN 下载离线包与校验清单'
    mkdir -p "$PACKAGE_DIR"
    download "$OFFLINE_BASE_URL/SHA256SUMS" "$PACKAGE_DIR/SHA256SUMS" \
        "$MANIFEST_SHA256"

    local file
    for file in \
        images.tar.zst data-system.tar.zst postgres-all.sql.zst \
        quantmind_qwenpaw-data.tar.zst quantmind_qwenpaw-secrets.tar.zst \
        quantmind_qwenpaw-backups.tar.zst quantmind_qwenpaw-shared.tar.zst \
        images.list README.txt; do
        grep -Eq "^[0-9a-f]{64}  ${file}$" "$PACKAGE_DIR/SHA256SUMS" \
            || die "离线包校验清单缺少: $file"
        download "$OFFLINE_BASE_URL/$file" "$PACKAGE_DIR/$file" \
            "$(awk -v name="$file" '$2 == name { print $1 }' "$PACKAGE_DIR/SHA256SUMS")"
    done
    (cd "$PACKAGE_DIR" && sha256sum --check --status SHA256SUMS) \
        || die '离线包 SHA-256 校验失败'
}

install_runtime() {
    log '步骤 1/8：更新系统并安装依赖'
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        ca-certificates curl git gnupg lsb-release zstd

    log '步骤 2/8：安装 Docker 和 Docker Compose'
    if ! command -v docker >/dev/null 2>&1; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io
    fi
    if ! docker compose version >/dev/null 2>&1; then
        # Ubuntu 源在不同版本中使用过两个包名。
        DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-plugin 2>/dev/null \
            || DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-v2
    fi
    systemctl enable --now docker
    docker compose version >/dev/null || die 'Docker Compose 不可用'

    configure_docker_mirror
}

configure_docker_mirror() {
    log "配置 Docker 镜像加速: $DOCKER_MIRROR"
    DOCKER_MIRROR="$DOCKER_MIRROR" python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path("/etc/docker/daemon.json")
try:
    config = json.loads(path.read_text()) if path.exists() else {}
except json.JSONDecodeError as exc:
    raise SystemExit(f"Docker 配置文件格式错误: {exc}")

mirror = os.environ["DOCKER_MIRROR"]
mirrors = [mirror] + [item for item in config.get("registry-mirrors", []) if item != mirror]
config["registry-mirrors"] = mirrors
path.parent.mkdir(parents=True, exist_ok=True)
temporary = path.with_suffix(".json.quantmind-tmp")
temporary.write_text(json.dumps(config, indent=2) + "\n")
temporary.replace(path)
PY
    systemctl restart docker
}

import_images() {
    local archive="$PACKAGE_DIR/images.tar.zst"
    log '步骤 4/8：解压并导入 Docker 镜像'
    zstd --test --quiet "$archive" || die '镜像包 zstd 校验失败'

    local image
    local images_ready=true
    for image in \
        quantmind-oss:latest quantmind-web:latest \
        quantmind-data-gateway:latest quantmind-dashboard:latest \
        postgres:15-alpine redis:7-alpine \
        lcomplete/huntly:latest diygod/rsshub:latest agentscope/qwenpaw:latest \
        python:3.10-slim-bookworm; do
        if ! docker image inspect "$image" >/dev/null 2>&1; then
            images_ready=false
            break
        fi
    done
    if $images_ready; then
        log '复用已导入的 Docker 镜像'
        return 0
    fi

    # 流式导入，不产生同等大小的中间 .tar 文件。
    zstd --decompress --stdout "$archive" | docker load

    for image in \
        quantmind-oss:latest quantmind-web:latest \
        quantmind-data-gateway:latest quantmind-dashboard:latest \
        postgres:15-alpine redis:7-alpine \
        lcomplete/huntly:latest diygod/rsshub:latest agentscope/qwenpaw:latest \
        python:3.10-slim-bookworm; do
        docker image inspect "$image" >/dev/null 2>&1 \
            || die "离线镜像包未包含必需镜像: $image"
    done
}

restore_payload_data() {
    local archive="$PACKAGE_DIR/data-system.tar.zst"
    rm -rf "$STAGING_DIR"
    mkdir -p "$STAGING_DIR"

    log '步骤 6/8：恢复业务数据、模型与 Qlib 数据'
    zstd --test --quiet "$archive" || die '业务数据包 zstd 校验失败'
    zstd --decompress --stdout "$archive" | tar --extract --file - --directory "$STAGING_DIR"
    [[ -f "$STAGING_DIR/db/qlib_data/calendars/day.txt" ]] \
        || die '业务数据包结构异常：缺少 db/qlib_data/calendars/day.txt'
}

has_qlib_features() {
    local qlib_dir="$1"
    [[ -f "$qlib_dir/calendars/day.txt" && -d "$qlib_dir/features" ]] \
        && find "$qlib_dir/features" -type f -print -quit 2>/dev/null | grep -q .
}

checkout_code() {
    log '步骤 5/8：下载最新代码'
    if [[ -e "$PROJECT_DIR" && ! -d "$PROJECT_DIR/.git" ]]; then
        die "部署目录已存在且不是 Git 仓库: $PROJECT_DIR"
    fi
    if [[ -d "$PROJECT_DIR/.git" ]]; then
        git -C "$PROJECT_DIR" fetch origin "$REF"
        git -C "$PROJECT_DIR" checkout --detach "origin/$REF" 2>/dev/null \
            || git -C "$PROJECT_DIR" checkout --detach "$REF"
    else
        git clone --branch "$REF" --depth 1 "$REPO_URL" "$PROJECT_DIR"
    fi

    # 发布分支尚未合并部署修复时，允许由受控的本地文件覆盖 Compose。
    # 该入口只覆盖此单一文件，避免把服务器上的任意目录复制进代码仓库。
    if [[ -n "$COMPOSE_OVERLAY" ]]; then
        [[ -f "$COMPOSE_OVERLAY" ]] || die "Compose 覆盖文件不存在: $COMPOSE_OVERLAY"
        cp "$COMPOSE_OVERLAY" "$PROJECT_DIR/docker-compose.yml"
        log "已应用 Compose 覆盖文件"
    fi
    if [[ -n "$DEPLOY_OVERLAY_DIR" ]]; then
        [[ -d "$DEPLOY_OVERLAY_DIR" ]] || die "部署覆盖目录不存在: $DEPLOY_OVERLAY_DIR"
        local relative_path
        for relative_path in \
            docker/Dockerfile.oss \
            docker/Dockerfile.web \
            docker/Dockerfile.data-gateway \
            docker/Dockerfile.dashboard; do
            if [[ -f "$DEPLOY_OVERLAY_DIR/$relative_path" ]]; then
                install -D -m 0644 "$DEPLOY_OVERLAY_DIR/$relative_path" \
                    "$PROJECT_DIR/$relative_path"
            fi
        done
        log "已应用 Dockerfile 覆盖层"
    fi

}

install_payload_data() {
    local qlib_target="$PROJECT_DIR/db/qlib_data"
    # 只有真实 Qlib 数据才默认保留；仓库中的空目录或损坏数据会被离线包替换。
    if [[ -e "$qlib_target" ]] && has_qlib_features "$qlib_target" \
        && [[ ${QUANTMIND_REPLACE_QLIB:-false} != true ]]; then
        log "检测到有效 Qlib 数据，复用现有目录: $qlib_target"
    else
        rm -rf "$qlib_target"
        mkdir -p "$PROJECT_DIR/db"
        mv "$STAGING_DIR/db/qlib_data" "$qlib_target"
    fi

    for directory in data models; do
        [[ -d "$STAGING_DIR/$directory" ]] || die "业务数据包缺少: $directory"
        if [[ -e "$PROJECT_DIR/$directory" ]] \
            && [[ ${QUANTMIND_REPLACE_BUSINESS_DATA:-false} != true ]]; then
            log "检测到已有 $directory，保留现有数据"
        else
            rm -rf "$PROJECT_DIR/$directory"
            mv "$STAGING_DIR/$directory" "$PROJECT_DIR/$directory"
        fi
    done
    rm -rf "$STAGING_DIR"
}

restore_database() {
    local archive="$PACKAGE_DIR/postgres-all.sql.zst"
    log '步骤 7/8：恢复 PostgreSQL 业务数据'
    cd "$PROJECT_DIR"
    docker compose up -d db
    local attempt
    for attempt in {1..30}; do
        if docker exec quantmind-db sh -lc 'pg_isready -U "$POSTGRES_USER"' >/dev/null 2>&1; then
            break
        fi
        sleep 2
    done
    docker exec quantmind-db sh -lc 'pg_isready -U "$POSTGRES_USER"' >/dev/null \
        || die 'PostgreSQL 未在规定时间内就绪'

    local table_count
    table_count="$(docker exec quantmind-db sh -lc \
        'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM pg_tables WHERE schemaname = '\''public'\''"')"
    if [[ "$table_count" != 0 && ${QUANTMIND_REPLACE_DATABASE:-false} != true ]]; then
        log "检测到已有 PostgreSQL 数据（$table_count 张表），保留现有数据"
        return 0
    fi
    zstd --decompress --stdout "$archive" \
        | docker exec -i quantmind-db sh -lc 'psql -U "$POSTGRES_USER"'
}

restore_qwenpaw_volumes() {
    local volume file
    for volume in data secrets backups shared; do
        file="$PACKAGE_DIR/quantmind_qwenpaw-$volume.tar.zst"
        docker volume create "quantmind_qwenpaw-$volume" >/dev/null
        if docker run --rm -v "quantmind_qwenpaw-$volume":/target \
            --entrypoint sh postgres:15-alpine \
            -c 'find /target -mindepth 1 -print -quit | grep -q .'; then
            if [[ ${QUANTMIND_REPLACE_QWENPAW_DATA:-false} != true ]]; then
                log "检测到已有 QwenPaw 卷，保留现有数据: $volume"
                continue
            fi
        fi
        zstd --decompress --stdout "$file" | docker run --rm -i \
            -v "quantmind_qwenpaw-$volume":/target \
            --entrypoint sh postgres:15-alpine -c 'tar -C /target -xf -'
    done
}

build_and_start() {
    log '步骤 8/8：基于最新代码重新构建并启动服务'
    cd "$PROJECT_DIR"
    # 核心镜像按最新代码重建。web/data-gateway/dashboard 已在离线包中提供
    # 成品镜像，直接复用可避免为可选服务拉取额外构建基础镜像。
    docker compose build --pull=false quantmind
    docker compose up -d --pull never
    docker compose ps
}

main() {
    require_root
    require_ubuntu
    require_url "$OFFLINE_BASE_URL" QUANTMIND_OFFLINE_BASE_URL
    install_runtime
    download_offline_package
    import_images
    checkout_code
    restore_payload_data
    install_payload_data
    restore_database
    restore_qwenpaw_volumes
    build_and_start
    log "完成：代码=$PROJECT_DIR，Qlib 数据=$PROJECT_DIR/db/qlib_data"
    echo ""
    echo "========================================================================="
    echo " 🎉 QuantMind 离线部署成功！"
    echo " -------------------------------------------------------------------------"
    echo " 🌐 Web 控制台  : http://<服务器 IP>:3000"
    echo " 📖 API 文档    : http://<服务器 IP>:8000/docs"
    echo " 👤 默认账号    : admin / admin123"
    echo " -------------------------------------------------------------------------"
    echo " 💡 【数据更新与扩展提示】"
    echo " 1. QuantDB 在线下载及日常增量更新（推荐）："
    echo "    在 Web 端【个人中心】->【数据平台】配置 API Key，"
    echo "    或在终端执行: docker exec quantmind python backend/scripts/quantdb_daily_sync.py"
    echo " 2. 百度网盘完整历史数据包（备选）："
    echo "    链接: https://pan.baidu.com/s/5IT4p5nFlglZ7zu_0H_fA8Q"
    echo "========================================================================="
    echo ""
}

main "$@"

