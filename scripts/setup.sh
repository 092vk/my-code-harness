#!/usr/bin/env bash
# Install graph-first navigation into a repository.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
HARNESS_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "setup.sh: python3 is required (the installer edits JSONC safely)." >&2
  exit 1
fi

exec python3 "$SCRIPT_DIR/lib/install.py" setup --harness-dir "$HARNESS_DIR" "$@"
