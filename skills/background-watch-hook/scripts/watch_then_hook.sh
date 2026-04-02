#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  watch_then_hook.sh --session-key <session_key> [options] -- <waiter command...>

Options:
  --session-key <value>      Required. Target session key for vibe hook send.
  --prefix <value>           Optional. Text prepended before waiter stdout.
  --timeout <seconds>        Optional. Overall waiter timeout. Default: 21600 (6 hours), 0 means forever.
  --post-to <value>          Optional. Passed through to vibe hook send.
  --deliver-key <value>      Optional. Explicit delivery target key for vibe hook send.
  --log-file <path>          Optional. Background log file path when detaching.
  --foreground               Optional. Run inline instead of detaching.
  --hook-bin <value>         Optional. Hook CLI executable. Overrides auto-detection.
  --hook-cmd <value>         Optional. Full hook command prefix. Example: 'uv run python -m vibe'
  --timeout-exit-code <n>    Optional. Exit code treated as timeout. Default: 124
  -h, --help                 Show this help.

Behavior:
  - By default, detaches immediately and keeps the waiter running in the background.
  - Use --foreground to run inline as a low-level primitive.
  - Captures waiter stdout to a temporary file.
  - If the waiter exits 0, builds a prompt file and runs vibe hook send.
  - If the waiter exits with the timeout code, still sends a timeout hook summary.
  - Any other non-zero exit is treated as failure and no hook is sent.
EOF
}

session_key=""
prefix=""
timeout_seconds="21600"
post_to=""
deliver_key=""
log_file=""
foreground=0
hook_bin="${VIBE_HOOK_BIN:-}"
hook_cmd_override="${VIBE_HOOK_CMD:-}"
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
    --timeout)
      timeout_seconds="${2:-21600}"
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
    --log-file)
      log_file="${2:-}"
      shift 2
      ;;
    --foreground)
      foreground=1
      shift
      ;;
    --hook-bin)
      hook_bin="${2:-}"
      shift 2
      ;;
    --hook-cmd)
      hook_cmd_override="${2:-}"
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

if [[ -n "$post_to" && "$post_to" != "thread" && "$post_to" != "channel" ]]; then
  echo "--post-to must be either 'thread' or 'channel'" >&2
  exit 2
fi

if [[ -n "$post_to" && -n "$deliver_key" ]]; then
  echo "--post-to and --deliver-key are mutually exclusive" >&2
  exit 2
fi

script_path="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"

output_file="$(mktemp)"
prompt_file="$(mktemp)"
cleanup() {
  rm -f "$output_file" "$prompt_file"
}
trap cleanup EXIT

_resolve_hook_command() {
  if [[ -n "$hook_cmd_override" ]]; then
    local override_help=""
    if [[ "$hook_cmd_override" == *" "* ]]; then
      override_help="$(bash -lc "$hook_cmd_override hook send --help" 2>/dev/null || true)"
    else
      override_help="$("$hook_cmd_override" hook send --help 2>/dev/null || true)"
    fi
    if [[ "$override_help" == *"--session-key"* && "$override_help" == *"Queue one asynchronous turn"* ]]; then
      printf '%s\n' "$hook_cmd_override"
      return 0
    fi
    echo "Hook command override did not expose a working 'hook send' command: $hook_cmd_override" >&2
    return 127
  fi

  if [[ -n "$hook_bin" ]]; then
    if ! command -v "$hook_bin" >/dev/null 2>&1; then
      echo "Hook binary not found: $hook_bin" >&2
      return 127
    fi
    printf '%s\n' "$hook_bin"
    return 0
  fi

  if command -v vibe >/dev/null 2>&1; then
    local help_output=""
    help_output="$(vibe hook send --help 2>/dev/null || true)"
    if [[ "$help_output" == *"--session-key"* && "$help_output" == *"Queue one asynchronous turn"* ]]; then
      printf '%s\n' "vibe"
      return 0
    fi
  fi

  if command -v uv >/dev/null 2>&1 && [[ -f pyproject.toml ]] && [[ -f vibe/__main__.py ]]; then
    local uv_help=""
    uv_help="$(uv run python -m vibe hook send --help 2>/dev/null || true)"
    if [[ "$uv_help" == *"--session-key"* && "$uv_help" == *"Queue one asynchronous turn"* ]]; then
      printf '%s\n' "uv run python -m vibe"
      return 0
    fi
  fi

  if command -v git >/dev/null 2>&1 && command -v uv >/dev/null 2>&1; then
    local worktree_path=""
    local worktree_help=""
    while IFS= read -r worktree_path; do
      [[ -z "$worktree_path" ]] && continue
      [[ "$worktree_path" == "$PWD" ]] && continue
      [[ -f "$worktree_path/pyproject.toml" ]] || continue
      [[ -f "$worktree_path/vibe/__main__.py" ]] || continue
      worktree_help="$(
        bash -lc "cd $(printf '%q' "$worktree_path") && uv run python -m vibe hook send --help" 2>/dev/null || true
      )"
      if [[ "$worktree_help" == *"--session-key"* && "$worktree_help" == *"Queue one asynchronous turn"* ]]; then
        printf 'cd %q && uv run python -m vibe\n' "$worktree_path"
        return 0
      fi
    done < <(git worktree list --porcelain | awk '/^worktree /{print substr($0, 10)}')
  fi

  echo "Could not resolve a working Vibe hook command. Set --hook-cmd or --hook-bin explicitly." >&2
  return 127
}

_run_hook_command() {
  local hook_cmd_prefix="$1"
  shift
  local -a hook_args=("$@")

  if [[ "$hook_cmd_prefix" == *" "* ]]; then
    local quoted_args=""
    local arg
    for arg in "${hook_args[@]}"; do
      quoted_args+=" $(printf '%q' "$arg")"
    done
    bash -lc "$hook_cmd_prefix$quoted_args"
    return
  fi

  "$hook_cmd_prefix" "${hook_args[@]}"
}

if [[ "$foreground" -ne 1 ]]; then
  hook_runner="$(_resolve_hook_command)"
  if [[ -z "$log_file" ]]; then
    log_file="/tmp/background-watch-hook-$(date +%Y%m%d%H%M%S)-$$.log"
  fi

  child_args=(
    "$script_path"
    --foreground
    --session-key "$session_key"
    --hook-cmd "$hook_runner"
    --timeout "$timeout_seconds"
    --timeout-exit-code "$timeout_exit_code"
  )

  if [[ -n "$prefix" ]]; then
    child_args+=(--prefix "$prefix")
  fi
  if [[ -n "$post_to" ]]; then
    child_args+=(--post-to "$post_to")
  fi
  if [[ -n "$deliver_key" ]]; then
    child_args+=(--deliver-key "$deliver_key")
  fi

  child_args+=(-- "$@")

  nohup "${child_args[@]}" >"$log_file" 2>&1 </dev/null &
  child_pid=$!

  printf 'Started background watch.\n'
  printf 'PID: %s\n' "$child_pid"
  printf 'Log file: %s\n' "$log_file"
  exit 0
fi

hook_runner="$(_resolve_hook_command)"
started_at_epoch="$(date +%s)"
timed_out=0

set +e
if [[ "$timeout_seconds" == "0" ]]; then
  "$@" >"$output_file"
  waiter_status=$?
else
  python3 - "$output_file" "$timeout_seconds" "$timeout_exit_code" "$@" <<'PY'
import subprocess
import sys

output_path = sys.argv[1]
timeout_seconds = float(sys.argv[2])
timeout_exit_code = int(sys.argv[3])
command = sys.argv[4:]

with open(output_path, "wb") as output_file:
    try:
        result = subprocess.run(command, stdout=output_file, timeout=timeout_seconds, check=False)
    except subprocess.TimeoutExpired:
        sys.exit(timeout_exit_code)

sys.exit(result.returncode)
PY
  waiter_status=$?
fi
set -e

if [[ "$waiter_status" -eq "$timeout_exit_code" ]]; then
  timed_out=1
  elapsed_seconds="$(( $(date +%s) - started_at_epoch ))"
  {
    printf 'Waiter timed out after %s second(s).\n' "$elapsed_seconds"
    printf 'Timeout exit code: %s\n' "$timeout_exit_code"
  } >"$output_file"
fi

if [[ "$waiter_status" -ne 0 && "$timed_out" -ne 1 ]]; then
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

hook_args=(hook send --session-key "$session_key" --prompt-file "$prompt_file")
if [[ -n "$post_to" ]]; then
  hook_args+=(--post-to "$post_to")
fi
if [[ -n "$deliver_key" ]]; then
  hook_args+=(--deliver-key "$deliver_key")
fi

_run_hook_command "$hook_runner" "${hook_args[@]}"
