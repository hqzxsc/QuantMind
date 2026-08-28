#!/bin/bash
# 原样启动官方 FutuOpenD（程序不改动，仅通过官方配置文件 FutuOpenD.xml 调整监听地址）
# 首次登录：docker attach futu-opend 按提示输入账号/密码/验证码（富途 APP 确认），
# 分离用 Ctrl+P → Ctrl+Q；登录态持久化在 /opt/futu/data
set -e
OPD_DIR=$(find /opt/futu -name FutuOpenD -type f | head -1 | xargs dirname)
cd "$OPD_DIR"

# 官方配置文件：API 监听 0.0.0.0（容器内），供宿主机/局域网经端口映射访问
if [ -f FutuOpenD.xml ]; then
    sed -i 's|<ip>127.0.0.1</ip>|<ip>0.0.0.0</ip>|' FutuOpenD.xml
fi

mkdir -p /opt/futu/data
export LD_LIBRARY_PATH="$OPD_DIR:$LD_LIBRARY_PATH"
echo "[entrypoint] starting official FutuOpenD: $OPD_DIR/FutuOpenD (api listen 0.0.0.0:11111)"
exec ./FutuOpenD -data_dir /opt/futu/data "$@"
