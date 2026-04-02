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
  --deliver-key <value>      Optional. Explicit delivery target key for vibe hook send.
  --hook-bin <value>         Optional. Hook CLI executable. Overrides auto-detection.
  --hook-cmd <value>         Optional. Full hook command prefix. Example: 'uv run python -m vibe'
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

output_file="$(mktemp)"
prompt_file="$(mktemp)"
cleanup() {
  rm -f "$output_file" "$prompt_file"
}
trap cleanup EXIT

_resolve_hook_command() {
  if [[ -n "$hook_cmd_override" ]]; then
    printf '%s\n' "$hook_cmd_override"
    return 0
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

hook_runner="$(_resolve_hook_command)"
started_at_epoch="$(date +%s)"
timed_out=0

set +e
"$@" >"$output_file"
waiter_status=$?
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
