# QuantMind 训练镜像 — AutoDL 公共模板发布版
#
# 审核要求：
#   - /root 下包含 GitHub 代码仓库 → /root/QuantMind（git clone）
#   - 仓库中的简单代码可成功执行 → /usr/local/bin/smoke.sh（跑真实训练预处理+LightGBM）
#
# 构建（本机有 NVIDIA GPU + nvidia-container-toolkit）：
#   docker build -f docker/autodl-public/Dockerfile -t quantmind-public:gpu .
#
# 本地验证（挂 GPU）：
#   docker run --rm --gpus all quantmind-public:gpu smoke.sh
#
# 镜像体积约 24GB（torch CUDA 全量），公共模板在 AutoDL 控制台
# 「保存镜像为公共镜像」入口提交，或经阿里云 ACR 公开仓库共享。

FROM docker.1ms.run/nvidia/cuda:12.1.0-base-ubuntu22.04

# 训练依赖安装走阿里云 PyPI 镜像（国内网络）；torch GPU 版走官方 cu128 索引
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG TORCH_INDEX_URL=https://mirrors.aliyun.com/pytorch-wheels/cu128
ARG QUANTMIND_REPO=https://github.com/qusong0627/QuantMind.git
ARG QUANTMIND_BRANCH=master

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=mirrors.aliyun.com \
    PYTHONPATH=/root/QuantMind

RUN sed -i 's/archive.ubuntu.com/mirrors.aliyun.com/g; s/security.ubuntu.com/mirrors.aliyun.com/g' /etc/apt/sources.list && \
    apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        python3 \
        python3-pip \
        python3-dev \
        build-essential \
        libgomp1 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 训练依赖：torch GPU 版（CUDA 12.8 wheel，向后兼容 12.1+ 驱动）+ 树模型全家桶
RUN python3 -m pip install --upgrade pip setuptools wheel \
    && python3 -m pip install --no-cache-dir --root-user-action=ignore "torch==2.9.1" \
        --index-url ${TORCH_INDEX_URL} \
        --extra-index-url ${PIP_INDEX_URL} \
    && python3 -m pip install --no-cache-dir --root-user-action=ignore \
        lightgbm xgboost catboost scikit-learn \
        pandas numpy pyarrow requests pyyaml \
        pytest jupyter

# /root 下 git clone GitHub 仓库（公共模板审核项）
RUN mkdir -p /root/QuantMind \
    && git clone --depth 1 --branch ${QUANTMIND_BRANCH} ${QUANTMIND_REPO} /root/QuantMind \
    && cd /root/QuantMind \
    && git remote -v \
    && git log --oneline -1

# 构建期自测脚本（审核要求：仓库中简单代码可执行）
COPY docker/autodl-public/smoke.sh /usr/local/bin/smoke.sh
RUN chmod +x /usr/local/bin/smoke.sh \
    && smoke.sh

WORKDIR /root/QuantMind
CMD ["bash", "-lc", "echo 'QuantMind 训练环境就绪（GPU 容器运行时需 --gpus all）'; echo '自测: smoke.sh'; cd /root/QuantMind && exec bash"]
