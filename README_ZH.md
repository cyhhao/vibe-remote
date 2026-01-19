<div align="center">

<img src="assets/logo.png" alt="Vibe Remote" width="40"/>

# Vibe Remote

[快速开始](#快速开始) · [配置](#配置) · [使用方式](#使用方式) · [安装指南](#setup-guides) · [Roadmap](#roadmap)

[![Python](https://img.shields.io/badge/python-3.9%2B-3776AB)](https://www.python.org/)
[![Platforms](https://img.shields.io/badge/platforms-Slack-4A90E2)](#setup-guides)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![PRs](https://img.shields.io/badge/PRs-welcome-brightgreen)](CONTRIBUTING.md)

[English](README.md) | [中文](README_ZH.md)

![Banner](assets/banner.jpg)

</div>

_在 Slack 里通过聊天远程操控 AI 代理（如 OpenCode、Claude Code、Codex、Cursor），进行 Vibe Coding。_

Vibe Remote 把 AI 写代码搬到聊天软件。你在 Slack 输入意图与约束，它会驱动相应的 AI agent 执行并反馈；结果实时流式返回，无需本地 IDE，随时随地推进任务。

## 为什么选择 Vibe Remote

- **专注 vibe coding**：基于你的意图与约束让 AI 自主推进，你只把控方向与结果。
- **随时随地**：不被 IDE 束缚，直接在 Slack 中远程操控编码会话。
- **为扩展而生**：OpenCode 优先，同时支持 Claude Code + Codex；并可扩展到更多 coding agents/CLIs。
- **多 Agent 路由**：通过 Slack UI 为不同 Channel 选择 OpenCode / Claude Code / Codex。
- **按线程 + 路径持久化**：每个 Slack 线程维持独立 Agent 会话与工作目录，并通过持久化映射自动恢复。
- **Slack 交互式体验**：`/start` 菜单 + Settings/CWD 模态，按钮优先于命令，更快上手。

> 推荐：优先使用 Slack 作为主要平台。其线程模型更适合并行子任务，也能保持频道历史整洁（每个子任务都在各自的线程里）。

## 核心特性

- **平台优先**：V2 先聚焦 Slack；平台抽象保留以支持未来 Vibe App
- **免干预工作流**：最小 review，实时流式回传消息
- **持久会话**：按聊天/线程维度持久化，可随时恢复
- **Slack 线程化 UX**：每个会话独立线程，保持频道整洁
- **工作目录控制**：随时查看与更改 `cwd`
- **个性化**：按频道自定义隐藏消息类型（默认隐藏 system/toolcall）

## 架构（简述）

- `BaseIMClient` + 平台实现（`modules/im/slack.py`）
- `IMFactory` 通过配置创建客户端
- `Controller` 统一编排会话、格式化与命令路由

## 先决条件

- 至少安装一个 Agent CLI。推荐优先使用 OpenCode（`opencode`），同时也支持 Claude Code 与 Codex，方便在不同频道切换。

### OpenCode（推荐）

安装（Homebrew）：

```bash
brew install opencode
```

安装（脚本）：

```bash
curl -fsSL https://opencode.ai/install | bash
```

验证：

```bash
opencode --help
```

在 Vibe Remote 中启用：

- `OPENCODE_ENABLED=true`
- 可选：`OPENCODE_CLI_PATH=opencode`、`OPENCODE_PORT=4096`

### Claude Code

安装：

```bash
npm install -g @anthropic-ai/claude-code
```

验证：

```bash
claude --help
```

## 快速开始

1. 安装

```bash
curl -fsSL https://vibe.remote/install.sh | bash
```

2. 运行

```bash
vibe
```

## 配置

### Slack

- `SLACK_BOT_TOKEN`（xoxb-...）
- `SLACK_APP_TOKEN`（xapp-...，用于 Socket Mode）
- `SLACK_TARGET_CHANNELS` 可选的频道 ID 白名单（仅频道，形如 `C...`）。留空或省略为接受所有频道。当前不支持 Slack DM。

### Claude Code

- `AGENT_DEFAULT_CWD` 例如 `./_tmp`（推荐）
- 兼容别名：`CLAUDE_DEFAULT_CWD`（仍支持）
- `CLAUDE_PERMISSION_MODE` 例如 `bypassPermissions`
- `CLAUDE_SYSTEM_PROMPT` 可选
- `ANTHROPIC_API_KEY`（取决于你的 SDK 设置）

### Codex

- 安装并登录 [Codex CLI](https://github.com/openai/codex)（执行 `codex --help` 验证）。
- `CODEX_ENABLED=true`（默认）启用 Codex；若环境没有 CLI 才需要设为 false。`CODEX_CLI_PATH` 可重定向可执行文件。
- `CODEX_DEFAULT_MODEL` / `CODEX_EXTRA_ARGS` 可强制模型或追加命令行参数。

### OpenCode

- 通过 `OPENCODE_ENABLED=true` 启用 OpenCode（默认 false），并确保已安装 `opencode`。
- Vibe Remote 会启动本地 OpenCode HTTP Server（`opencode serve --hostname=127.0.0.1 --port=4096`）。
- OpenCode 的默认 agent/model 配置读取自 `~/.config/opencode/opencode.json`；在 Slack 下也可通过 Agent Settings 对每个 channel 单独覆盖。

### Agent 路由

- Slack：推荐直接使用内置的 **Agent Settings** 弹窗，为每个 channel 选择 backend。
- Slack 使用频道 ID。
- 参见 [docs/CODEX_SETUP.md](docs/CODEX_SETUP.md) 获取 Codex 安装与路由说明。
- 如果启用了 OpenCode，则默认使用 OpenCode；否则回退到 Claude。

### 应用

- `LOG_LEVEL` 默认 `INFO`

## 使用方式

### Commands

- `/start` 打开菜单/欢迎信息
- `/clear` 重置对话/会话
- `/cwd` 显示工作目录
- `/set_cwd <path>` 更改工作目录
- `/settings` 配置消息可见性
- `/stop` 强制停止当前 Agent（Claude 发送 interrupt，Codex 直接终止进程）

### Subagent 前缀路由

消息开头使用 `SubagentName:` 或 `SubagentName：`（允许前置空格/换行），即可调用当前频道绑定 Agent 的 Subagent。

- 示例：`Plan: 先把实现步骤列出来`
- 匹配大小写不敏感，仅在当前绑定的 Agent 内查找
- 自动使用 Subagent 的默认 model / reasoning_effort
- 命中时机器人会在消息上加 🤖 reaction

### Slack

- 在频道中运行 `/start` 打开交互菜单（Current Dir、Change Work Dir、Reset Session、Settings、How it Works）
- 机器人会把每次对话组织到各自的线程中；在线程中继续回复即可
- 当前不支持 Slack DM
- Slash 命令在线程中受限；要在线程内停止，请直接输入 `stop`

## Setup Guides

- Slack： [English](docs/SLACK_SETUP.md) | [中文](docs/SLACK_SETUP_ZH.md)

## Releases

统一以 GitHub Releases 作为变更记录：https://github.com/cyhhao/vibe-remote/releases

## Roadmap

- 扩展到更多编码 CLI/agents（超越当前内置 Agent）
- 更多 IM 平台（Discord、Teams）
- 文件上传/附件到编码会话的管道化
- 更细粒度的会话策略与权限

## Contributing

参见 `CONTRIBUTING.md`。参与即代表同意 `CODE_OF_CONDUCT.md`。

## License

MIT，详见 `LICENSE`。

## Security & Ops

- **Secrets**：不要提交 Token；存储在 `~/.vibe_remote/config/config.json` 或你的密钥管理系统。
- **Whitelists**：通过 `SLACK_TARGET_CHANNELS`（仅频道，`C…`）限制访问。留空允许全部频道（Slack DM 当前不支持）。
- **Logs**：运行日志位于 `~/.vibe_remote/logs/vibe_remote.log`。
- **会话持久化**：`~/.vibe_remote/state/sessions.json` 存储每个线程的会话映射；生产环境请持久化此文件。
- **清理**：设置 `CLEANUP_ENABLED=true`，在消息处理入口安全清理已完成的接收任务，适合长时间运行。
