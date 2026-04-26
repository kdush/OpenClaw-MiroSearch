ARG PYTHON_BASE_IMAGE=python:3.12-slim
FROM ${PYTHON_BASE_IMAGE}

ARG UV_VERSION=0.7.8
ARG APP_WORKDIR=/workspace/apps/gradio-demo
# Debian apt 镜像源域名，可在构建时覆盖；留空则保留官方 deb.debian.org
# 默认使用清华园镜像，规避离线环境或外网受限时 deb.debian.org 连接超时问题
ARG APT_MIRROR=mirrors.tuna.tsinghua.edu.cn

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=300 \
    UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

# 兼容 deb822（trixie 起 /etc/apt/sources.list.d/debian.sources）与老 sources.list 两种格式
RUN if [ -n "${APT_MIRROR}" ]; then \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g; s|security.debian.org|${APT_MIRROR}|g" \
            /etc/apt/sources.list /etc/apt/sources.list.d/*.sources 2>/dev/null || true; \
    fi && \
    apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple uv==${UV_VERSION}

WORKDIR /workspace
COPY . /workspace

WORKDIR ${APP_WORKDIR}
RUN uv sync --frozen --no-dev || uv sync --frozen --no-dev

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --retries=5 --start-period=40s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/gradio_api/info', timeout=5).read()" || exit 1

CMD [".venv/bin/python", "main.py"]
