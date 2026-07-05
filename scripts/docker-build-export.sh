#!/usr/bin/env bash
# 在 macOS（Apple Container）或 Docker 环境下构建镜像并导出 tar，供绿联 NAS 离线导入。
#
# Apple Container 的 `container image save` 输出 OCI 布局（含 index.json），
# 绿联 NAS 的 `docker load` 需要 Docker 格式（含 manifest.json）。
# 使用 Apple Container 时会自动经 skopeo 转换为 Docker 格式。
#
# 用法：
#   ./scripts/docker-build-export.sh              # 默认 0.3.1 / linux/amd64
#   ./scripts/docker-build-export.sh 0.3.0 linux/arm64
#   PLATFORM=linux/amd64 ./scripts/docker-build-export.sh
#
# 输出：dist/tradingagents-<version>-<platform>.tar（Docker load 兼容）

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-0.3.2}"
PLATFORM="${2:-${PLATFORM:-linux/amd64}}"
IMAGE="tradingagents:${VERSION}"
SAFE_PLATFORM="${PLATFORM//\//-}"
OUT_DIR="${ROOT}/dist"
OUT_FILE="${OUT_DIR}/tradingagents-${VERSION}-${SAFE_PLATFORM}.tar"

# linux/arm64 -> arm64
ARCH="${PLATFORM#*/}"
OS="${PLATFORM%%/*}"

mkdir -p "${OUT_DIR}"

find_skopeo() {
  if command -v skopeo >/dev/null 2>&1; then
    command -v skopeo
    return 0
  fi
  for candidate in /opt/homebrew/bin/skopeo /usr/local/bin/skopeo; do
    if [[ -x "${candidate}" ]]; then
      echo "${candidate}"
      return 0
    fi
  done
  return 1
}

verify_docker_tar() {
  if ! tar -tf "${OUT_FILE}" | grep -qx 'manifest.json'; then
    echo "错误：${OUT_FILE} 不含 manifest.json，绿联 docker load 无法识别。" >&2
    exit 1
  fi
}

if command -v container >/dev/null 2>&1; then
  RUNNER="Apple Container"
  SKOPEO="$(find_skopeo || true)"
  if [[ -z "${SKOPEO}" ]]; then
    echo "错误：Apple Container 导出为 OCI 格式，需 skopeo 转换为 Docker 格式。" >&2
    echo "请安装：brew install skopeo" >&2
    exit 1
  fi

  OCI_TMP="${OUT_DIR}/.tmp-${SAFE_PLATFORM}-oci.tar"
  trap 'rm -f "${OCI_TMP}"' EXIT

  echo "==> 使用 ${RUNNER} 构建 ${IMAGE}（平台 ${PLATFORM}）"
  container build -t "${IMAGE}" -f "${ROOT}/Dockerfile" --platform "${PLATFORM}" "${ROOT}"

  echo "==> 导出 OCI 临时包"
  container image save -o "${OCI_TMP}" "${IMAGE}"

  echo "==> 经 skopeo 转换为 Docker 格式（供 docker load 使用）"
  "${SKOPEO}" --override-os "${OS}" --override-arch "${ARCH}" copy \
    "oci-archive:${OCI_TMP}" \
    "docker-archive:${OUT_FILE}:${IMAGE}"

elif command -v docker >/dev/null 2>&1; then
  RUNNER="Docker"
  echo "==> 使用 ${RUNNER} 构建 ${IMAGE}（平台 ${PLATFORM}）"
  docker build -t "${IMAGE}" -f "${ROOT}/Dockerfile" --platform "${PLATFORM}" "${ROOT}"
  echo "==> 导出 Docker 格式镜像"
  docker save -o "${OUT_FILE}" "${IMAGE}"
else
  echo "错误：未找到 container（Apple Container）或 docker 命令。" >&2
  exit 1
fi

verify_docker_tar
ls -lh "${OUT_FILE}"
echo ""
echo "完成（${RUNNER} → Docker load 兼容 tar）。复制到 NAS 后执行："
echo "  docker load -i $(basename "${OUT_FILE}")"
