# IM Message Consolidation & Toolcall Semantics

## 背景 / 目标

项目目前支持 3 种 Agent 后端：

1. Claude Code
2. Codex
3. OpenCode

它们都会产生一些共通的消息类型（System / Assistant / Toolcall / Result / Notify）。

本改动的目标是：

- **System / Assistant / Toolcall**：在同一个 Slack thread 内，只维护并更新 **一条可编辑消息**（append-only 视角）。
- **Result**：单独发送一条消息，并且 **永远发送**（不可隐藏）。
- **Notify**：保持现状（warning/error 及时通知，逐条发送）。

## Message Types 定义

- `notify`
  - 用途：错误/告警/提示
  - 行为：始终独立发送，不参与合并

- `result`
  - 用途：最终结果（带 duration）
  - 行为：始终独立发送，不参与合并；Message Visibility 中不提供开关

- `system` / `assistant` / `toolcall`
  - 用途：过程消息
  - 行为：合并为同一条“Consolidated Message”，后续内容追加并通过 edit 更新

## Consolidated Message 行为

- Key：按会话上下文聚合（Slack 用 thread_id）。
- 追加规则：每条新内容在末尾追加，并以 `---` 分隔。
- 截断规则：超长时丢弃最早内容，并在开头加 `…(truncated)…`。
  - Slack：默认保留最后 35000 字符
- Visibility：当 `system/assistant/toolcall` 全部被 hide 时，不发送/不更新 consolidated。

## Toolcall 定义

Toolcall 是“Agent 调用工具”的简洁表示：

- 单行输出
- 格式：`tool_name + params(JSON)`

实现入口：`BaseMarkdownFormatter.format_toolcall()`。

## Agent 实现要点

### Claude Code

- Toolcall：从 `ToolUseBlock` 提取，并发出 `toolcall`。
- Assistant：仅展示 `TextBlock`。
- 去重：最后一条 assistant 可能被 result fallback 使用时，assistant 采用“pending buffer”策略，避免重复。

### Codex

- Toolcall：将 `command_execution` 映射为 `toolcall`（仅展示 command + status）。
- 去重：最后一条 assistant message 作为 result 时不重复发送 assistant（pending buffer）。

### OpenCode

- 使用 `POST /session/{id}/prompt_async` 启动长任务，避免同步 HTTP 卡死。
- 轮询 `GET /session/{id}/message`：
  - `parts[].type == "tool"`：即时发出 toolcall（即使 message 未 completed）。
  - 最终 text：仅在 result 中发送，避免和 assistant 重复。

## 配置 / 兼容性

- OpenCode 单次 HTTP 请求超时固定为 60s（目前不通过 `.env` 配置），用于轮询等请求，不限制任务运行时长。
- 历史配置兼容：旧的 `response/user` message type 会 canonicalize 为 `toolcall`。
