# Vibe Remote CLI 参考手册

## 快速开始

```bash
vibe              # 启动 Vibe Remote（打开 Web UI）
vibe status       # 查看服务状态
vibe screenshot   # 截取本机桌面截图
vibe stop         # 停止所有服务
```

## 命令详解

## 远端服务器访问 Web UI（SSH 端口转发）

默认情况下，Web UI 只监听在运行 Vibe Remote 的那台机器的 `127.0.0.1:5123`。

如果你把 Vibe Remote 部署在远端服务器上，**不建议把 UI 端口直接暴露到公网**。
推荐使用 SSH 本地端口转发，把远端的 UI 端口安全地映射到你自己的电脑上访问。

### 1）在服务器上启动 Vibe Remote

先 SSH 登录到服务器并启动：

```bash
vibe
```

此时 UI 仍然只能在服务器本机访问：

- `http://127.0.0.1:5123`

### 2）把 UI 端口转发到本机

在你自己的电脑（本地）执行：

```bash
ssh -NL 5123:localhost:5123 user@server-ip
```

然后在本机浏览器打开：

- `http://127.0.0.1:5123`

### 小贴士

- 如果你本机的 `5123` 端口已被占用，可以换一个本地端口：

```bash
ssh -NL 15123:localhost:5123 user@server-ip
```

然后打开 `http://127.0.0.1:15123`。

- 如果服务器 SSH 端口不是 22：

```bash
ssh -p 2222 -NL 5123:localhost:5123 user@server-ip
```

- `-N` 表示不在远端执行命令，这条 SSH 连接只用于建立隧道。


### `vibe`

启动或重启 Vibe Remote。会在浏览器中打开 Web UI。

```bash
vibe
```

**行为：**
- 如果已在运行，则重启主服务
- 打开设置向导 `http://127.0.0.1:5123`
- **保留 OpenCode 服务器** — 正在执行的任务不会被中断

### `vibe stop`

完全停止所有 Vibe Remote 服务。

```bash
vibe stop
```

**行为：**
- 停止主服务
- 停止 Web UI 服务器
- **终止 OpenCode 服务器** — 当你需要重启 OpenCode 时使用此命令

### `vibe status`

显示当前服务状态。

```bash
vibe status
```

**输出示例：**
```json
{
  "state": "running",
  "running": true,
  "pid": 12345
}
```

### `vibe doctor`

运行配置诊断检查。

```bash
vibe doctor
```

**检查内容：**
- 配置文件有效性
- Slack token 配置
- Agent CLI 可用性（Claude Code、OpenCode、Codex）
- 运行时环境

### `vibe screenshot`

截取本机桌面并保存为 PNG 文件。

```bash
vibe screenshot
vibe screenshot --output /tmp/screen.png
vibe screenshot --json
```

**行为：**
- 默认保存到 `~/.vibe_remote/screenshots/`
- 默认输出保存路径；加 `--json` 时输出机器可读的 JSON
- 只作为 CLI 层能力存在；不新增 IM 命令、bot 按钮，也不注入 Agent prompt

### `vibe task`

创建、查看、更新、立即执行、暂停、恢复或删除定时任务。

```bash
vibe task add --session-key 'slack::channel::C123' --cron '0 * * * *' --prompt 'Share the hourly summary.'
vibe task list --brief
vibe task update <task-id> --cron '*/30 * * * *'
vibe task run <task-id>
vibe task remove <task-id>
```

更完整的参数说明请直接看 `vibe task add --help` 和 `vibe task update --help`。其中重点包括：

- 用 `--session-key` 指定会话连续性
- 用 `--post-to channel` 在保留 thread 上下文的同时把消息发到父频道
- 用 `--deliver-key` 指定显式投递目标
- 用 `--cron` / `--at` 控制定时方式
- 以及 `--name`、`--timezone`、`--prompt-file` 等参数

### `vibe hook send`

队列化一次异步 turn，不会把任务定义持久化到 `scheduled_tasks.json`。

```bash
vibe hook send --session-key 'slack::channel::C123' --prompt 'The export finished. Share the summary.'
vibe hook send --session-key 'slack::channel::C123::thread::171717.123' --post-to channel --prompt 'Share the benchmark result in the channel.'
```

适合“只异步补发一次消息，不想保存成定时任务”的场景。

### `vibe version`

显示已安装的版本。

```bash
vibe version
```

### `vibe check-update`

检查是否有新版本可用。

```bash
vibe check-update
```

### `vibe upgrade`

升级到最新版本。

```bash
vibe upgrade
```

## 服务生命周期

### 理解「重启」与「停止」的区别

Vibe Remote 管理两类进程：

| 进程 | 说明 |
|------|------|
| **主服务** | 处理各聊天平台通信，并将消息路由到 Agent |
| **OpenCode 服务器** | OpenCode Agent 的后端服务（如已启用） |

命令的关键区别：

| 命令 | 主服务 | OpenCode 服务器 |
|------|--------|-----------------|
| `vibe restart` | 重启 | **终止** |
| `vibe stop` | 停止 | **终止** |

### 为什么这很重要

当你运行 `vibe restart` 时：
- 主服务会被干净地重启
- UI 也会一起重启
- OpenCode 服务器会在重启过程中被终止

当你运行 `vibe stop` 时：
- **一切都会干净地停止**
- OpenCode 服务器被终止
- 更新 OpenCode 或其配置前使用此命令

## 常见场景

### 日常重启

如果是 Agent 在当前会话里触发重启，默认优先用延迟参数，用户体验更好：

```bash
vibe restart --delay-seconds 60
```

如果就是要立刻重启 Vibe Remote：

```bash
vibe restart
```

### 更新 OpenCode 配置

修改 `~/.config/opencode/opencode.json` 后：

```bash
vibe restart --delay-seconds 60
```

### 更新 OpenCode 程序

安装新版本 OpenCode 后：

```bash
vibe restart --delay-seconds 60
```

### 更新 Vibe Remote

```bash
vibe upgrade
# 然后重启：
vibe restart --delay-seconds 60
```

### 故障排查

如果遇到卡住的情况：

```bash
# 检查状态
vibe status

# 运行诊断
vibe doctor

# 如果是 Agent 触发，优先延迟重启
vibe restart --delay-seconds 60
```

## Web UI 控制

Web UI (`http://127.0.0.1:5123`) 提供相同的控制功能：

| 按钮 | 等效 CLI | OpenCode 行为 |
|------|---------|---------------|
| **Start** | `vibe` | 按需启动 |
| **Restart** | `vibe restart` | 终止 |
| **Stop** | `vibe stop` | 终止 |

## 文件位置

| 路径 | 说明 |
|------|------|
| `~/.vibe_remote/config/config.json` | 主配置文件 |
| `~/.vibe_remote/state/settings.json` | 频道路由设置 |
| `~/.vibe_remote/state/scheduled_tasks.json` | 持久化的定时任务定义 |
| `~/.vibe_remote/state/task_requests/` | task run 与 hook 的请求队列 |
| `~/.vibe_remote/state/user_preferences.md` | 共享的长期用户偏好笔记 |
| `~/.vibe_remote/logs/vibe_remote.log` | 应用日志 |
| `~/.vibe_remote/logs/opencode_server.json` | OpenCode 服务器 PID 文件 |

## 环境变量

| 变量 | 说明 |
|------|------|
| `OPENCODE_PORT` | 覆盖 OpenCode 服务器端口（默认：4096） |

## 另请参阅

- [Slack 配置指南](SLACK_SETUP_ZH.md)
- [Telegram 配置指南](TELEGRAM_SETUP_ZH.md)
- [Codex 配置指南](CODEX_SETUP.md)
