#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WRAPPER="$SCRIPT_DIR/watch_then_hook.sh"
WAITER="$SCRIPT_DIR/wait_for_github_pr_activity.py"

usage() {
  cat <<'EOF'
Usage:
  watch_github_pr_then_hook.sh --session-key <session_key> --repo <owner/name> --pr <number> [options]

Options:
  --session-key <value>           Required. Target session key for vibe hook send.
  --repo <value>                  Required. GitHub repo in owner/name form.
  --pr <number>                   Required. Pull request number.
  --prefix <value>                Optional. Hook prefix. If omitted, a sensible default is used.
  --interval <seconds>            Optional. Polling interval passed to the waiter.
  --timeout <seconds>             Optional. Overall waiter timeout. Default: 21600 (6 hours).
  --event-limit <count>           Optional. Maximum rendered events.
  --catch-up                      Optional. Treat existing PR activity as pending immediately.
  --allow-unauthenticated         Optional. Allow throttled best-effort polling without GitHub auth.
  --since-review-id <id>          Optional. Review cursor.
  --since-review-comment-id <id>  Optional. Review-comment cursor.
  --since-issue-comment-id <id>   Optional. PR conversation comment cursor.
  --log-file <path>               Optional. Background log file path.
  --foreground                    Optional. Run inline instead of detaching.
  --post-to <thread|channel>      Optional. Passed through to the wrapper.
  --deliver-key <value>           Optional. Passed through to the wrapper.
  --hook-bin <value>              Optional. Passed through to the wrapper.
  --hook-cmd <value>              Optional. Passed through to the wrapper.
  --timeout-exit-code <n>         Optional. Must remain 124 for the bundled waiter.
  -h, --help                      Show this help.
EOF
}

session_key=""
repo=""
pr=""
prefix=""
interval=""
timeout=""
event_limit=""
catch_up=0
allow_unauthenticated=0
since_review_id=""
since_review_comment_id=""
since_issue_comment_id=""
log_file=""
foreground=0
post_to=""
deliver_key=""
hook_bin=""
hook_cmd=""
timeout_exit_code=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session-key)
      session_key="${2:-}"
      shift 2
      ;;
    --repo)
      repo="${2:-}"
      shift 2
      ;;
    --pr)
      pr="${2:-}"
      shift 2
      ;;
    --prefix)
      prefix="${2:-}"
      shift 2
      ;;
    --interval)
      interval="${2:-}"
      shift 2
      ;;
    --timeout)
      timeout="${2:-}"
      shift 2
      ;;
    --event-limit)
      event_limit="${2:-}"
      shift 2
      ;;
    --catch-up)
      catch_up=1
      shift
      ;;
    --allow-unauthenticated)
      allow_unauthenticated=1
      shift
      ;;
    --since-review-id)
      since_review_id="${2:-}"
      shift 2
      ;;
    --since-review-comment-id)
      since_review_comment_id="${2:-}"
      shift 2
      ;;
    --since-issue-comment-id)
      since_issue_comment_id="${2:-}"
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
      hook_cmd="${2:-}"
      shift 2
      ;;
    --timeout-exit-code)
      timeout_exit_code="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$session_key" || -z "$repo" || -z "$pr" ]]; then
  echo "--session-key, --repo, and --pr are required" >&2
  usage >&2
  exit 2
fi

if [[ -n "$timeout_exit_code" && "$timeout_exit_code" != "124" ]]; then
  echo "--timeout-exit-code must remain 124 for the bundled GitHub waiter" >&2
  exit 2
fi

if [[ ! -x "$WRAPPER" ]]; then
  echo "Wrapper not found or not executable: $WRAPPER" >&2
  exit 127
fi

if [[ ! -x "$WAITER" ]]; then
  echo "Waiter not found or not executable: $WAITER" >&2
  exit 127
fi

if [[ -z "$prefix" ]]; then
  prefix="GitHub PR #$pr has new review activity. Fetch the latest review state, summarize actionable items, and continue handling them if needed."
fi

if [[ -z "$timeout" ]]; then
  timeout="21600"
fi

wrapper_args=("$WRAPPER" --session-key "$session_key" --prefix "$prefix")

if [[ -n "$timeout" ]]; then
  wrapper_args+=(--timeout "$timeout")
fi
if [[ -n "$log_file" ]]; then
  wrapper_args+=(--log-file "$log_file")
fi
if [[ "$foreground" -eq 1 ]]; then
  wrapper_args+=(--foreground)
fi
if [[ -n "$post_to" ]]; then
  wrapper_args+=(--post-to "$post_to")
fi
if [[ -n "$deliver_key" ]]; then
  wrapper_args+=(--deliver-key "$deliver_key")
fi
if [[ -n "$hook_bin" ]]; then
  wrapper_args+=(--hook-bin "$hook_bin")
fi
if [[ -n "$hook_cmd" ]]; then
  wrapper_args+=(--hook-cmd "$hook_cmd")
fi
if [[ -n "$timeout_exit_code" ]]; then
  wrapper_args+=(--timeout-exit-code "$timeout_exit_code")
fi

waiter_args=("$WAITER" --repo "$repo" --pr "$pr")

if [[ -n "$interval" ]]; then
  waiter_args+=(--interval "$interval")
fi
if [[ -n "$timeout" ]]; then
  waiter_args+=(--timeout "$timeout")
fi
if [[ -n "$event_limit" ]]; then
  waiter_args+=(--event-limit "$event_limit")
fi
if [[ "$catch_up" -eq 1 ]]; then
  waiter_args+=(--catch-up)
fi
if [[ "$allow_unauthenticated" -eq 1 ]]; then
  waiter_args+=(--allow-unauthenticated)
fi
if [[ -n "$since_review_id" ]]; then
  waiter_args+=(--since-review-id "$since_review_id")
fi
if [[ -n "$since_review_comment_id" ]]; then
  waiter_args+=(--since-review-comment-id "$since_review_comment_id")
fi
if [[ -n "$since_issue_comment_id" ]]; then
  waiter_args+=(--since-issue-comment-id "$since_issue_comment_id")
fi

"${wrapper_args[@]}" -- "${waiter_args[@]}"
