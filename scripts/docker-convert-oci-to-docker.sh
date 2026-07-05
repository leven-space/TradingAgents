#!/usr/bin/env bash
# 将 Apple Container 导出的 OCI tar 转为绿联 NAS docker load 可识别的 Docker tar。
#
# 用法：
#   ./scripts/docker-convert-oci-to-docker.sh dist/tradingagents-0.3.0-linux-arm64.tar
#   ./scripts/docker-convert-oci-to-docker.sh input.tar output.tar tradingagents:0.3.0 linux arm64

set -euo pipefail

INPUT="${1:?请指定 OCI tar 路径}"
OUTPUT="${2:-${INPUT%.tar}-docker.tar}"
IMAGE="${3:-tradingagents:0.3.0}"
OS="${4:-linux}"
ARCH="${5:-arm64}"

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

SKOPEO="$(find_skopeo || true)"
if [[ -z "${SKOPEO}" ]]; then
  echo "请先安装 skopeo：brew install skopeo" >&2
  exit 1
fi

"${SKOPEO}" --override-os "${OS}" --override-arch "${ARCH}" copy \
  "oci-archive:${INPUT}" \
  "docker-archive:${OUTPUT}:${IMAGE}"

if tar -tf "${OUTPUT}" | grep -qx 'manifest.json'; then
  echo "已生成 Docker 格式：${OUTPUT}"
  ls -lh "${OUTPUT}"
else
  echo "转换失败：${OUTPUT} 不含 manifest.json" >&2
  exit 1
fi
