#!/usr/bin/env bash
# Remove opencode-graph-harness from a repository.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "uninstall.sh: python3 is required." >&2
  exit 1
fi

exec python3 "$SCRIPT_DIR/lib/install.py" uninstall "$@"
