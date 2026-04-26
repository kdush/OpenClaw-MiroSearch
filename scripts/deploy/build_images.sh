#!/usr/bin/env bash
# 构建 api-server / gradio-demo 容器镜像（兼容容器构建网络受限场景）。
#
# 背景：
#   docker compose build 在 BuildKit/buildx 下，若 compose.yaml 用 build.network: host，
#   会触发交互式 entitlements 授权（"additional privileges requested"），ssh 非 TTY
#   场景下无法通过；此脚本直接调 `docker build --network=host`，绕过授权流程。
#
# 用法：
#   scripts/deploy/build_images.sh           # 构建 api 与 demo
#   scripts/deploy/build_images.sh api       # 仅构建 api / worker 共用的 api-server 镜像
#   scripts/deploy/build_images.sh demo      # 仅构建 gradio-demo 镜像
#
# 环境变量：
#   APT_MIRROR        覆盖 Dockerfile 内默认的 apt 镜像源（默认清华园）
#   PIP_INDEX_URL     覆盖 PyPI 镜像源
#   IMAGE_TAG_API     覆盖 api-server 镜像 tag（默认 openclaw-mirosearch-api:latest）
#   IMAGE_TAG_DEMO    覆盖 gradio-demo 镜像 tag（默认 openclaw-mirosearch:latest）

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

TARGET="${1:-all}"
IMAGE_TAG_API="${IMAGE_TAG_API:-openclaw-mirosearch-api:latest}"
IMAGE_TAG_DEMO="${IMAGE_TAG_DEMO:-openclaw-mirosearch:latest}"

BUILD_ARGS=()
[[ -n "${APT_MIRROR:-}" ]] && BUILD_ARGS+=("--build-arg" "APT_MIRROR=${APT_MIRROR}")
[[ -n "${PIP_INDEX_URL:-}" ]] && BUILD_ARGS+=("--build-arg" "PIP_INDEX_URL=${PIP_INDEX_URL}")

build_api() {
    echo ">>> 构建 ${IMAGE_TAG_API}"
    DOCKER_BUILDKIT=1 docker build \
        --network=host \
        "${BUILD_ARGS[@]}" \
        -f apps/api-server/Dockerfile \
        -t "${IMAGE_TAG_API}" \
        .
}

build_demo() {
    echo ">>> 构建 ${IMAGE_TAG_DEMO}"
    DOCKER_BUILDKIT=1 docker build \
        --network=host \
        "${BUILD_ARGS[@]}" \
        -f apps/gradio-demo/Dockerfile \
        -t "${IMAGE_TAG_DEMO}" \
        .
}

case "$TARGET" in
    api)
        build_api
        ;;
    demo)
        build_demo
        ;;
    all)
        build_api
        build_demo
        ;;
    *)
        echo "用法: $0 [api|demo|all]" >&2
        exit 2
        ;;
esac

echo
echo "构建完成。如需重启服务："
echo "  docker compose up -d api worker app"
