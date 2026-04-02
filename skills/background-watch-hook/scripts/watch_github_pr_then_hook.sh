#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_PATH="$SCRIPT_DIR/$(basename "${BASH_SOURCE[0]}")"
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
  --timeout <seconds>             Optional. Per-cycle waiter timeout. Default: 21600 (6 hours).
  --lifetime-timeout <seconds>    Optional. Overall lifetime for --forever. Default: 0 (run until killed).
  --forever                       Optional. Keep watching and re-arm after each detected event.
  --event-limit <count>           Optional. Maximum rendered events.
  --catch-up                      Optional. Treat existing PR activity as pending immediately.
  --allow-unauthenticated         Optional. Allow throttled best-effort polling without GitHub auth.
  --since-review-id <id>          Optional. Review cursor.
  --since-review-comment-id <id>  Optional. Review-comment cursor.
  --since-issue-comment-id <id>   Optional. PR conversation comment cursor.
  --since-reaction-id <id>        Optional. PR-body reaction cursor.
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
lifetime_timeout="0"
forever=0
event_limit=""
catch_up=0
allow_unauthenticated=0
since_review_id=""
since_review_comment_id=""
since_issue_comment_id=""
since_reaction_id=""
log_file=""
foreground=0
post_to=""
deliver_key=""
hook_bin=""
hook_cmd=""
timeout_exit_code=""

normalize_nonnegative_number() {
  python3 - "$1" <<'PY'
import math
import sys

value = float(sys.argv[1])
if not math.isfinite(value) or value < 0:
    raise SystemExit(1)
if value.is_integer():
    print(int(value))
else:
    print(value)
PY
}

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
    --lifetime-timeout)
      lifetime_timeout="${2:-}"
      shift 2
      ;;
    --forever)
      forever=1
      shift
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
    --since-reaction-id)
      since_reaction_id="${2:-}"
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

if [[ -z "$timeout" ]]; then
  timeout="21600"
fi
if ! timeout="$(normalize_nonnegative_number "$timeout")"; then
  echo "--timeout must be a finite number >= 0" >&2
  exit 2
fi
if ! lifetime_timeout="$(normalize_nonnegative_number "$lifetime_timeout")"; then
  echo "--lifetime-timeout must be a finite number >= 0" >&2
  exit 2
fi

if [[ "$forever" -ne 1 && "$lifetime_timeout" != "0" ]]; then
  echo "--lifetime-timeout requires --forever" >&2
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

if [[ "$forever" -eq 1 && "$foreground" -ne 1 ]]; then
  if [[ -z "$log_file" ]]; then
    log_file="/tmp/background-watch-github-pr-$(date +%Y%m%d%H%M%S)-$$.log"
  fi

  child_args=(
    "$SCRIPT_PATH"
    --foreground
    --forever
    --session-key "$session_key"
    --repo "$repo"
    --pr "$pr"
    --prefix "$prefix"
    --timeout "$timeout"
    --lifetime-timeout "$lifetime_timeout"
  )

  if [[ -n "$interval" ]]; then
    child_args+=(--interval "$interval")
  fi
  if [[ -n "$event_limit" ]]; then
    child_args+=(--event-limit "$event_limit")
  fi
  if [[ "$catch_up" -eq 1 ]]; then
    child_args+=(--catch-up)
  fi
  if [[ "$allow_unauthenticated" -eq 1 ]]; then
    child_args+=(--allow-unauthenticated)
  fi
  if [[ -n "$since_review_id" ]]; then
    child_args+=(--since-review-id "$since_review_id")
  fi
  if [[ -n "$since_review_comment_id" ]]; then
    child_args+=(--since-review-comment-id "$since_review_comment_id")
  fi
  if [[ -n "$since_issue_comment_id" ]]; then
    child_args+=(--since-issue-comment-id "$since_issue_comment_id")
  fi
  if [[ -n "$since_reaction_id" ]]; then
    child_args+=(--since-reaction-id "$since_reaction_id")
  fi
  if [[ -n "$post_to" ]]; then
    child_args+=(--post-to "$post_to")
  fi
  if [[ -n "$deliver_key" ]]; then
    child_args+=(--deliver-key "$deliver_key")
  fi
  if [[ -n "$hook_bin" ]]; then
    child_args+=(--hook-bin "$hook_bin")
  fi
  if [[ -n "$hook_cmd" ]]; then
    child_args+=(--hook-cmd "$hook_cmd")
  fi

  nohup "${child_args[@]}" >"$log_file" 2>&1 </dev/null &
  child_pid=$!

  printf 'Started forever GitHub PR watch.\n'
  printf 'PID: %s\n' "$child_pid"
  printf 'Log file: %s\n' "$log_file"
  printf 'Lifecycle: this watcher stays alive until killed.\n'
  exit 0
fi

send_status_hook() {
  local status_prefix="$1"
  local status_message="$2"

  local -a status_args=(
    "$WRAPPER"
    --foreground
    --session-key "$session_key"
    --prefix "$status_prefix"
    --timeout 0
  )

  if [[ -n "$post_to" ]]; then
    status_args+=(--post-to "$post_to")
  fi
  if [[ -n "$deliver_key" ]]; then
    status_args+=(--deliver-key "$deliver_key")
  fi
  if [[ -n "$hook_bin" ]]; then
    status_args+=(--hook-bin "$hook_bin")
  fi
  if [[ -n "$hook_cmd" ]]; then
    status_args+=(--hook-cmd "$hook_cmd")
  fi

  status_args+=(-- python3 -c 'import sys; print(sys.argv[1])' "$status_message")
  "${status_args[@]}"
}

if [[ "$forever" -eq 1 ]]; then
  cursor_file="$(mktemp)"
  retry_delay_seconds=30
  lifetime_started_at="$(date +%s)"
  cleanup() {
    rm -f "$cursor_file"
  }
  trap cleanup EXIT

  current_since_review_id="$since_review_id"
  current_since_review_comment_id="$since_review_comment_id"
  current_since_issue_comment_id="$since_issue_comment_id"
  current_since_reaction_id="$since_reaction_id"
  current_catch_up="$catch_up"

  while true; do
    cycle_timeout="$timeout"
    if [[ "$lifetime_timeout" != "0" ]]; then
      lifetime_window="$(
        python3 - "$lifetime_started_at" "$lifetime_timeout" <<'PY'
import sys
import time

started_at = float(sys.argv[1])
lifetime_timeout = float(sys.argv[2])
remaining = lifetime_timeout - (time.time() - started_at)
print(max(0.0, remaining))
PY
      )"

      if python3 - "$lifetime_window" <<'PY'
import sys

raise SystemExit(0 if float(sys.argv[1]) <= 0 else 1)
PY
      then
        elapsed_seconds="$(
          python3 - "$lifetime_started_at" <<'PY'
import sys
import time

print(int(max(0, time.time() - float(sys.argv[1]))))
PY
        )"
        send_status_hook \
          "GitHub PR watch stopped after reaching its lifetime timeout." \
          "Lifetime timeout reached after ${elapsed_seconds} second(s) for ${repo}#${pr}. Last cursors: review=${current_since_review_id:-0} review_comment=${current_since_review_comment_id:-0} issue_comment=${current_since_issue_comment_id:-0} reaction=${current_since_reaction_id:-0}."
        exit 0
      fi

      cycle_timeout="$(
        python3 - "$timeout" "$lifetime_window" <<'PY'
import sys

cycle_timeout = float(sys.argv[1])
remaining_lifetime = float(sys.argv[2])
if cycle_timeout <= 0:
    print(remaining_lifetime)
else:
    print(min(cycle_timeout, remaining_lifetime))
PY
      )"
    fi

    rm -f "$cursor_file"

    cycle_wrapper_args=(
      "$WRAPPER"
      --foreground
      --session-key "$session_key"
      --prefix "$prefix"
      --timeout 0
      --timeout-exit-code 125
    )

    if [[ -n "$post_to" ]]; then
      cycle_wrapper_args+=(--post-to "$post_to")
    fi
    if [[ -n "$deliver_key" ]]; then
      cycle_wrapper_args+=(--deliver-key "$deliver_key")
    fi
    if [[ -n "$hook_bin" ]]; then
      cycle_wrapper_args+=(--hook-bin "$hook_bin")
    fi
    if [[ -n "$hook_cmd" ]]; then
      cycle_wrapper_args+=(--hook-cmd "$hook_cmd")
    fi

    cycle_waiter_args=(
      "$WAITER"
      --repo "$repo"
      --pr "$pr"
      --timeout "$cycle_timeout"
      --cursor-output "$cursor_file"
    )

    if [[ -n "$interval" ]]; then
      cycle_waiter_args+=(--interval "$interval")
    fi
    if [[ -n "$event_limit" ]]; then
      cycle_waiter_args+=(--event-limit "$event_limit")
    fi
    if [[ "$allow_unauthenticated" -eq 1 ]]; then
      cycle_waiter_args+=(--allow-unauthenticated)
    fi
    if [[ -n "$current_since_review_id" ]]; then
      cycle_waiter_args+=(--since-review-id "$current_since_review_id")
    fi
    if [[ -n "$current_since_review_comment_id" ]]; then
      cycle_waiter_args+=(--since-review-comment-id "$current_since_review_comment_id")
    fi
    if [[ -n "$current_since_issue_comment_id" ]]; then
      cycle_waiter_args+=(--since-issue-comment-id "$current_since_issue_comment_id")
    fi
    if [[ -n "$current_since_reaction_id" ]]; then
      cycle_waiter_args+=(--since-reaction-id "$current_since_reaction_id")
    fi
    if [[ "$current_catch_up" -eq 1 ]]; then
      cycle_waiter_args+=(--catch-up)
    fi

    set +e
    "${cycle_wrapper_args[@]}" -- "${cycle_waiter_args[@]}"
    cycle_status=$?
    set -e

    if [[ "$cycle_status" -eq 124 ]]; then
      continue
    fi

    if [[ "$cycle_status" -ne 1 ]]; then
      echo "Forever watch stopped on non-retryable cycle failure: $cycle_status" >&2
      exit "$cycle_status"
    fi

    if [[ "$cycle_status" -ne 0 ]]; then
      echo "Forever watch cycle failed with status $cycle_status; retrying in ${retry_delay_seconds}s." >&2
      sleep "$retry_delay_seconds"
      continue
    fi

    if [[ ! -s "$cursor_file" ]]; then
      echo "No cursor output written; re-arming with existing cursors." >&2
      continue
    fi

    cursor_values="$(
      python3 - "$cursor_file" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
print(
    "%s\t%s\t%s\t%s"
    % (
        payload["review_cursor"],
        payload["review_comment_cursor"],
        payload["issue_comment_cursor"],
        payload.get("reaction_cursor", 0),
    )
)
PY
    )"
    IFS=$'\t' read -r current_since_review_id current_since_review_comment_id current_since_issue_comment_id current_since_reaction_id <<<"$cursor_values"
    current_catch_up=0
  done
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
if [[ -n "$since_reaction_id" ]]; then
  waiter_args+=(--since-reaction-id "$since_reaction_id")
fi

"${wrapper_args[@]}" -- "${waiter_args[@]}"
