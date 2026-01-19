# Slack Bot 安装指南

本指南将引导你为 Vibe Remote 设置 Slack Bot。

## 先决条件

- Slack 工作区管理员权限
- Python 3.9 或更高版本
- 已安装 Vibe Remote（`curl -fsSL https://raw.githubusercontent.com/cyhhao/vibe-remote/master/install.sh | bash`）

## 步骤 1：创建 Slack App

1. 访问 [https://api.slack.com/apps](https://api.slack.com/apps)
2. 点击 **"Create New App"** → **"From an app manifest"**
3. 选择你的工作区
4. 粘贴以下 manifest（YAML 格式）：

```yaml
display_information:
  name: Vibe Remote
  description: Local-first agent runtime for Slack
  background_color: "#0B1B2B"
features:
  bot_user:
    display_name: Vibe Remote
    always_online: false
  slash_commands:
    - command: /start
      description: Open main menu
      should_escape: false
    - command: /stop
      description: Stop current session
      should_escape: false
oauth_config:
  scopes:
    bot:
      - channels:history
      - channels:read
      - chat:write
      - app_mentions:read
      - users:read
      - commands
      - groups:read
      - groups:history
      - groups:write
      - chat:write.public
      - files:read
      - files:write
      - reactions:read
      - reactions:write
      - users:read.email
      - team:read
settings:
  event_subscriptions:
    bot_events:
      - message.channels
      - message.groups
      - app_mention
      - member_joined_channel
      - member_left_channel
      - channel_created
      - channel_renamed
      - team_join
  socket_mode_enabled: true
  interactivity:
    is_enabled: true
```

5. 点击 **"Next"**，检查配置，然后点击 **"Create"**

## 步骤 2：生成 Token

### Bot Token

1. 进入侧边栏的 **"OAuth & Permissions"**
2. 点击 **"Install to Workspace"**
3. 检查权限并点击 **"Allow"**
4. 复制 **Bot User OAuth Token**（以 `xoxb-` 开头）

### App Token（Socket Mode）

1. 进入侧边栏的 **"Basic Information"**
2. 滚动到 **"App-Level Tokens"**
3. 点击 **"Generate Token and Scopes"**
4. 名称：`socket-mode`
5. 添加 scope：`connections:write`
6. 点击 **"Generate"**
7. 复制 **App-Level Token**（以 `xapp-` 开头）

## 步骤 3：配置 Vibe Remote

运行 Vibe Remote 打开设置向导：

```bash
vibe
```

Web UI 将在 `http://localhost:5173` 打开。按照设置向导：

1. **选择模式**：选择 "Self-host (Socket Mode)"
2. **Agent 检测**：配置你的编码 Agent 后端（OpenCode、Claude、Codex）
3. **Slack 配置**：
   - 粘贴你的 **Bot Token**（`xoxb-...`）
   - 粘贴你的 **App Token**（`xapp-...`）
   - 点击 **"验证"** 测试连接
4. **频道设置**：启用你希望 Bot 工作的频道
5. **摘要**：检查并启动服务

## 步骤 4：邀请 Bot 到频道

在 Bot 能够与频道交互之前，需要邀请它：

1. 在 Slack 中进入频道
2. 输入 `/invite @Vibe Remote`
3. 按回车

## 步骤 5：测试 Bot

1. 在已启用的频道中，输入 `/start`
2. 你应该看到 Vibe Remote 菜单
3. 输入消息开始编码会话

## 使用方式

### Slash 命令

- `/start` - 打开带有交互按钮的主菜单
- `/stop` - 停止当前会话

### 交互菜单选项

`/start` 后，你会看到以下按钮：
- **Current Dir** - 显示当前工作目录
- **Change Work Dir** - 打开模态框更改工作目录
- **Reset Session** - 清除对话上下文
- **Settings** - 配置消息可见性
- **How it Works** - 显示帮助信息

### 基于线程的对话

- Bot 为每个对话创建线程
- 在线程中回复以继续会话
- 每个线程维护自己的 Agent 会话

## 故障排除

### Bot 不响应

1. 检查服务是否运行：`vibe status`
2. 检查日志：`~/.vibe_remote/logs/vibe_remote.log`
3. 运行诊断：`vibe doctor`
4. 验证 Bot 已被邀请到频道

### 权限错误

1. 验证 Token 设置正确（通过 Web UI 检查）
2. 确保 App manifest 中包含所有必需的 scope
3. 如果 scope 有变更，重新安装 App 到工作区

### Socket Mode 问题

1. 验证 App Token（`xapp-`）设置正确
2. 检查 App 设置中已启用 Socket Mode
3. 确保 app-level token 有 `connections:write` scope

### 频道访问问题

1. 确保 Bot 已被邀请到频道：`/invite @Vibe Remote`
2. 对于私有频道，验证 `groups:read`、`groups:history` scope
3. 检查频道已在 Web UI 中启用

## 安全注意事项

- Token 本地存储在 `~/.vibe_remote/config/config.json`
- 永远不要将 Token 提交到版本控制
- Web UI 仅在 localhost 运行
- 定期通过 Slack App 设置轮换你的 Token

## 手动配置（替代方式）

如果你更喜欢直接编辑配置文件而不是使用 Web UI：

编辑 `~/.vibe_remote/config/config.json`：

```json
{
  "mode": "self_host",
  "slack": {
    "bot_token": "xoxb-your-bot-token",
    "app_token": "xapp-your-app-token"
  },
  "runtime": {
    "default_cwd": "/path/to/your/project"
  },
  "agents": {
    "default_backend": "opencode",
    "opencode": {"enabled": true, "cli_path": "opencode"},
    "claude": {"enabled": true, "cli_path": "claude"},
    "codex": {"enabled": false, "cli_path": "codex"}
  }
}
```

然后启动服务：

```bash
vibe
```

## 其他资源

- [Slack API 文档](https://api.slack.com/)
- [Socket Mode 指南](https://api.slack.com/apis/connections/socket)
- [Vibe Remote GitHub](https://github.com/cyhhao/vibe-remote)
