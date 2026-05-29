#!/usr/bin/env bash
set -euo pipefail

UI_DIR="${1:-/opt/certsvc/ui}"
DIST_DIR="${UI_DIR}/dist"
SNAPSHOT_BASE="${UI_DIR}/release_snapshots"
STAMP="$(date +%Y%m%d_%H%M%S)"
TARGET_DIR="${SNAPSHOT_BASE}/dist_${STAMP}"

if [[ ! -d "${DIST_DIR}" ]]; then
  echo "[ERROR] dist directory not found: ${DIST_DIR}" >&2
  exit 1
fi

mkdir -p "${SNAPSHOT_BASE}"
cp -a "${DIST_DIR}" "${TARGET_DIR}"

cat > "${SNAPSHOT_BASE}/LATEST" <<EOF
${TARGET_DIR}
EOF

echo "[OK] UI dist snapshot created: ${TARGET_DIR}"
