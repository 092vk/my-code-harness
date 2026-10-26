#!/usr/bin/env bash
# Build or refresh the code graph for a repository.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
HARNESS_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"

BACKEND=""
TARGET=""
FORCE=0
STATUS=0
QUIET=0

usage() {
  cat <<'EOF'
Usage: index.sh [options] [PROJECT]

Build or refresh the persistent code graph for PROJECT. If PROJECT is omitted,
the current Git repository root is used (or the current directory).

Options:
  -b, --backend cbm|codegraph|both  Backend to use. Default: the backend recorded
                                    by setup.sh, otherwise cbm.
  -p, --project PATH                Repository to index.
  -f, --force                       Force a full CodeGraph re-index.
  -s, --status                      Show index status instead of indexing.
  -q, --quiet                       Suppress harness progress messages.
  -h, --help                        Show this help.

Examples:
  ./scripts/index.sh
  ./scripts/index.sh --backend both --project ~/src/linux
  ./scripts/index.sh --status /path/to/repo

Storage:
  codebase-memory-mcp  ~/.cache/codebase-memory-mcp (override CBM_CACHE_DIR)
  codegraph            PROJECT/.codegraph          (add it to .gitignore)

Environment:
  CBM_CACHE_DIR         Override the codebase-memory-mcp cache directory.
  CODEGRAPH_DIR         Override the CodeGraph data directory name.
  CODEGRAPH_TELEMETRY   Set to "off" to disable CodeGraph telemetry (default here).
EOF
}

log() {
  if [[ "$QUIET" -eq 0 ]]; then
    printf '%s\n' "$*"
  fi
}

die() {
  printf 'index.sh: %s\n' "$*" >&2
  exit 1
}

need_value() {
  if [[ $# -lt 2 || -z "${2:-}" ]]; then
    die "option $1 requires a value"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -b|--backend)
      need_value "$@"
      BACKEND="$2"
      shift 2
      ;;
    --backend=*)
      BACKEND="${1#*=}"
      shift
      ;;
    -p|--project)
      need_value "$@"
      TARGET="$2"
      shift 2
      ;;
    --project=*)
      TARGET="${1#*=}"
      shift
      ;;
    -f|--force)
      FORCE=1
      shift
      ;;
    -s|--status)
      STATUS=1
      shift
      ;;
    -q|--quiet)
      QUIET=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      die "unknown option: $1 (see --help)"
      ;;
    *)
      if [[ -n "$TARGET" ]]; then
        die "unexpected extra argument: $1"
      fi
      TARGET="$1"
      shift
      ;;
  esac
done

if [[ $# -gt 0 ]]; then
  if [[ -n "$TARGET" ]]; then
    die "unexpected extra argument: $1"
  fi
  TARGET="$1"
fi

if [[ -z "$TARGET" ]]; then
  if git rev-parse --show-toplevel >/dev/null 2>&1; then
    TARGET="$(git rev-parse --show-toplevel)"
  else
    TARGET="$PWD"
  fi
fi

[[ -d "$TARGET" ]] || die "project directory does not exist: $TARGET"
TARGET="$(cd -- "$TARGET" && pwd -P)"

MANIFEST="$TARGET/.opencode/graph-harness/manifest.json"
if [[ -z "$BACKEND" && -f "$MANIFEST" ]]; then
  BACKEND="$(sed -n 's/.*"backend"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$MANIFEST" | head -n 1)"
fi
BACKEND="${BACKEND:-cbm}"

case "$BACKEND" in
  cbm|codegraph|both) ;;
  codebase-memory|codebase-memory-mcp) BACKEND="cbm" ;;
  *) die "unknown backend: $BACKEND (expected cbm, codegraph, or both)" ;;
esac

if ! git -C "$TARGET" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  log "Warning: $TARGET is not a Git work tree; indexes will still be built."
fi

CBM_CMD=()
CG_CMD=()

resolve_cbm() {
  if command -v codebase-memory-mcp >/dev/null 2>&1; then
    CBM_CMD=(codebase-memory-mcp)
  elif command -v npx >/dev/null 2>&1; then
    CBM_CMD=(npx -y codebase-memory-mcp)
  else
    die "codebase-memory-mcp is not installed and npx is unavailable.
Install it with: npm install -g codebase-memory-mcp
Or install Node.js/npx for the on-demand wrapper."
  fi
}

resolve_codegraph() {
  if command -v codegraph >/dev/null 2>&1; then
    CG_CMD=(codegraph)
  elif command -v npx >/dev/null 2>&1; then
    CG_CMD=(npx -y @colbymchenry/codegraph)
  else
    die "codegraph is not installed and npx is unavailable.
Install it with: npm install -g @colbymchenry/codegraph
Or install Node.js/npx for the on-demand wrapper."
  fi
}

run_cbm() {
  resolve_cbm
  if [[ "$STATUS" -eq 1 ]]; then
    log "codebase-memory-mcp status:"
    "${CBM_CMD[@]}" cli list_projects --format json --detail stats
    return 0
  fi
  log "Indexing with codebase-memory-mcp"
  log "  project: $TARGET"
  log "  cache:   ${CBM_CACHE_DIR:-$HOME/.cache/codebase-memory-mcp}"
  "${CBM_CMD[@]}" cli index_repository --repo-path "$TARGET"
}

run_codegraph() {
  resolve_codegraph
  export CODEGRAPH_TELEMETRY="${CODEGRAPH_TELEMETRY:-off}"
  if [[ "$STATUS" -eq 1 ]]; then
    log "CodeGraph status:"
    "${CG_CMD[@]}" status "$TARGET"
    return 0
  fi
  log "Indexing with CodeGraph"
  log "  project: $TARGET"
  log "  data:    $TARGET/.codegraph"
  if [[ -d "$TARGET/.codegraph" ]]; then
    if [[ "$FORCE" -eq 1 ]]; then
      "${CG_CMD[@]}" index "$TARGET" --force
    else
      "${CG_CMD[@]}" index "$TARGET"
    fi
  else
    "${CG_CMD[@]}" init "$TARGET"
  fi
}

RESULT=0

case "$BACKEND" in
  cbm)
    run_cbm || RESULT=$?
    ;;
  codegraph)
    run_codegraph || RESULT=$?
    ;;
  both)
    if ! run_cbm; then
      RESULT=1
      log "codebase-memory-mcp indexing failed; continuing with CodeGraph."
    fi
    if ! run_codegraph; then
      RESULT=1
      log "CodeGraph indexing failed."
    fi
    ;;
esac

if [[ "$RESULT" -ne 0 ]]; then
  die "indexing did not complete (exit $RESULT)"
fi

if [[ "$STATUS" -eq 0 ]]; then
  log ""
  log "Index complete."
  log "Reload OpenCode if the MCP server was already running (/reload)."
  log "The graph-first rules are in $TARGET/AGENTS.md."
fi
