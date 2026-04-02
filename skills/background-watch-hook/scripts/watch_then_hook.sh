#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  watch_then_hook.sh --session-key <session_key> [options] -- <waiter command...>

Options:
  --session-key <value>      Required. Target session key for vibe hook send.
  --prefix <value>           Optional. Text prepended before waiter stdout.
  --post-to <value>          Optional. Passed through to vibe hook send.
  --deliver-key <value>      Optional. Passed through to vibe hook send.
  --hook-bin <value>         Optional. Hook CLI binary. Default: vibe
  --timeout-exit-code <n>    Optional. Silent exit code. Default: 124
  -h, --help                 Show this help.

Behavior:
  - Runs the waiter command synchronously.
  - Captures waiter stdout to a temporary file.
  - If the waiter exits 0, builds a prompt file and runs vibe hook send.
  - If the waiter exits with the timeout code, exits silently without sending a hook.
  - Any other non-zero exit is treated as failure and no hook is sent.
EOF
}

session_key=""
prefix=""
post_to=""
deliver_key=""
hook_bin="${VIBE_HOOK_BIN:-vibe}"
timeout_exit_code=124

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session-key)
      session_key="${2:-}"
      shift 2
      ;;
    --prefix)
      prefix="${2:-}"
      shift 2
      ;;
    --post-to)
      post_to="${2:-}"
      shift 2
      ;;
    --deliver-key)
      deliver_key="${2:-}"
      shift 2
      ;;
    --hook-bin)
      hook_bin="${2:-}"
      shift 2
      ;;
    --timeout-exit-code)
      timeout_exit_code="${2:-124}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$session_key" ]]; then
  echo "--session-key is required" >&2
  usage >&2
  exit 2
fi

if [[ $# -eq 0 ]]; then
  echo "A waiter command is required after --" >&2
  usage >&2
  exit 2
fi

if ! command -v "$hook_bin" >/dev/null 2>&1; then
  echo "Hook binary not found: $hook_bin" >&2
  exit 127
fi

output_file="$(mktemp)"
prompt_file="$(mktemp)"
cleanup() {
  rm -f "$output_file" "$prompt_file"
}
trap cleanup EXIT

set +e
"$@" >"$output_file"
waiter_status=$?
set -e

if [[ "$waiter_status" -eq "$timeout_exit_code" ]]; then
  exit 0
fi

if [[ "$waiter_status" -ne 0 ]]; then
  echo "Waiter exited with status $waiter_status" >&2
  exit "$waiter_status"
fi

if [[ ! -s "$output_file" && -z "$prefix" ]]; then
  echo "Waiter succeeded but produced no stdout; skipping hook" >&2
  exit 0
fi

{
  if [[ -n "$prefix" ]]; then
    printf '%s\n' "$prefix"
    if [[ -s "$output_file" ]]; then
      printf '\n'
    fi
  fi
  if [[ -s "$output_file" ]]; then
    cat "$output_file"
  fi
} >"$prompt_file"

hook_cmd=("$hook_bin" hook send --session-key "$session_key" --prompt-file "$prompt_file")
if [[ -n "$post_to" ]]; then
  hook_cmd+=(--post-to "$post_to")
fi
if [[ -n "$deliver_key" ]]; then
  hook_cmd+=(--deliver-key "$deliver_key")
fi

"${hook_cmd[@]}"
