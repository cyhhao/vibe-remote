# Feishu Claude + Mobile UI + Discord Menu Fixes

## Background

- In the three-end regression environment, the Feishu instance (`15133`) fails when Claude is selected as the backend.
- The observed runtime error is `Command failed with exit code 1`, with stderr indicating `--dangerously-skip-permissions cannot be used with root/sudo privileges for security reasons`.
- The mobile Web UI currently allows the fixed bottom tab bar to cover the last visible portion of page content.
- On Discord, successful menu submissions leave the original interactive message behind as `submitted`, even though a fresh success message is already sent.

## Goal

- Make Claude work in the containerized three-end regression environment without resetting user state.
- Prevent the mobile bottom navigation from obscuring page content.
- Dismiss successful Discord operation menus instead of replacing them with a `submitted` placeholder.

## Proposed Solution

1. Detect Claude sessions that run as root and pass `IS_SANDBOX=1` to the Claude subprocess when bypass permissions mode is active.
2. Add mobile bottom spacing that matches the fixed bottom navigation height and safe-area inset.
3. On successful Discord settings/routing submissions, delete the original interactive message and only fall back to `submitted` text if deletion fails.

## Todo

- [x] Add Claude container/root compatibility handling in the shared session layer.
- [x] Add a regression test for the Claude sandbox env decision.
- [x] Update the mobile shell layout to reserve space for the bottom nav.
- [x] Update Discord operation menu success handling to dismiss the original message.
- [x] Run targeted validation and regression checks.
