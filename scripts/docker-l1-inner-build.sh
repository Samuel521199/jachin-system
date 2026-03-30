#!/usr/bin/env bash
# 由 build-l1-linux-via-docker.ps1 在容器内调用；调用前已对 *.sh 做过 CRLF 清理
set -euo pipefail
REPO="${REPO_ROOT:-/repo}"
exec bash "$REPO/scripts/build-l1-linux-release.sh"
