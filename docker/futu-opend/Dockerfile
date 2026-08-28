# 富途官方 OpenD（Linux Ubuntu18.04 原包，未做任何修改）
# 构建前把官方下载的 FutuOpenD_ubuntu.tar.gz 放到本目录：
#   https://www.futunn.com/download/OpenD  （Linux/Ubuntu 版）
# 原包来源：https://www.futunn.com/download/fetch-lasted-link?name=opend-ubuntu
FROM ubuntu:18.04

ENV DEBIAN_FRONTEND=noninteractive
# OpenD 运行所需的基础库（官方文档要求）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libnss3 libfontconfig1 libfreetype6 libx11-6 libxext6 \
    libxrender1 zlib1g libcurl4 openssl ca-certificates telnet \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/futu

# 官方原包原样解压（不改任何文件）
COPY FutuOpenD_ubuntu.tar.gz /tmp/
RUN tar -xzf /tmp/FutuOpenD_ubuntu.tar.gz -C /opt/futu --strip-components=1 \
    && chmod +x /opt/futu/*/FutuOpenD 2>/dev/null; \
    find /opt/futu -name FutuOpenD -type f -exec chmod +x {} \; \
    && rm /tmp/FutuOpenD_ubuntu.tar.gz

# 登录态持久化目录（首次扫码/验证后长期有效）
ENV FUTU_DATA=/opt/futu/data
RUN mkdir -p /opt/futu/data

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 11111 11112

# OpenD 原样启动（stdin/tty 开启以支持首次登录交互输入验证码）
ENTRYPOINT ["/entrypoint.sh"]
