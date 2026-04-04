ARG PYTHON_BASE_IMAGE=python:3.12-slim
FROM ${PYTHON_BASE_IMAGE}

ARG UV_VERSION=0.7.8
ARG APP_WORKDIR=/workspace/apps/api-server

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==${UV_VERSION}

WORKDIR /workspace
COPY . /workspace

WORKDIR ${APP_WORKDIR}
RUN uv sync --frozen --no-dev

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=10s --retries=5 --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=5).read()" || exit 1

CMD ["uv", "run", "python", "main.py"]
