# Vibe Remote CLI 参考手册

## 快速开始

```bash
vibe              # 启动 Vibe Remote（打开 Web UI）
vibe status       # 查看服务状态
vibe stop         # 停止所有服务
```

## 命令详解

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
| **主服务** | 处理 Slack 通信，将消息路由到 Agent |
| **OpenCode 服务器** | OpenCode Agent 的后端服务（如已启用） |

命令的关键区别：

| 命令 | 主服务 | OpenCode 服务器 |
|------|--------|-----------------|
| `vibe` | 重启 | **保留** |
| `vibe stop` | 停止 | **终止** |

### 为什么这很重要

当你运行 `vibe` 重启时：
- **正在运行的 OpenCode 任务会继续执行**，不会中断
- 新的 Vibe Remote 实例会「认领」现有的 OpenCode 服务器
- 会话状态得以保留

当你运行 `vibe stop` 时：
- **一切都会干净地停止**
- OpenCode 服务器被终止
- 更新 OpenCode 或其配置前使用此命令

## 常见场景

### 日常重启

只想重启 Vibe Remote，不中断正在进行的工作：

```bash
vibe
```

### 更新 OpenCode 配置

修改 `~/.config/opencode/opencode.json` 后：

```bash
vibe stop && vibe
```

### 更新 OpenCode 程序

安装新版本 OpenCode 后：

```bash
vibe stop && vibe
```

### 更新 Vibe Remote

```bash
vibe upgrade
# 然后重启：
vibe stop && vibe
```

### 故障排查

如果遇到卡住的情况：

```bash
# 检查状态
vibe status

# 运行诊断
vibe doctor

# 完全重启（停止所有服务包括 OpenCode）
vibe stop && vibe
```

## Web UI 控制

Web UI (`http://127.0.0.1:5123`) 提供相同的控制功能：

| 按钮 | 等效 CLI | OpenCode 行为 |
|------|---------|---------------|
| **Start** | `vibe` | 保留 |
| **Restart** | `vibe` | 保留 |
| **Stop** | `vibe stop` | 终止 |

## 文件位置

| 路径 | 说明 |
|------|------|
| `~/.vibe_remote/config/config.json` | 主配置文件 |
| `~/.vibe_remote/state/settings.json` | 频道路由设置 |
| `~/.vibe_remote/logs/vibe_remote.log` | 应用日志 |
| `~/.vibe_remote/logs/opencode_server.json` | OpenCode 服务器 PID 文件 |

## 环境变量

| 变量 | 说明 |
|------|------|
| `OPENCODE_PORT` | 覆盖 OpenCode 服务器端口（默认：4096） |

## 另请参阅

- [Slack 配置指南](SLACK_SETUP_ZH.md)
- [Codex 配置指南](CODEX_SETUP.md)
