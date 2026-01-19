# Slack 配置指南（5 分钟）

## 太长不看

1. 用下面的 manifest 创建 Slack App
2. 拿到两个 token（`xoxb-` 和 `xapp-`）
3. 运行 `vibe`，在网页上粘贴

---

## 第 1 步：创建 App

1. 打开 [api.slack.com/apps](https://api.slack.com/apps)
2. **Create New App** → **From an app manifest**
3. 选择工作区
4. 粘贴这段 YAML：

```yaml
display_information:
  name: Vibe Remote
  description: AI coding agent for Slack
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
      - chat:write.public
      - app_mentions:read
      - users:read
      - commands
      - groups:read
      - groups:history
      - groups:write
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

5. 点 **Create**

---

## 第 2 步：获取 Token

### Bot Token（`xoxb-`）

1. **OAuth & Permissions** → **Install to Workspace** → **Allow**
2. 复制 **Bot User OAuth Token**

### App Token（`xapp-`）

1. **Basic Information** → **App-Level Tokens** → **Generate Token**
2. 名称：`socket-mode`
3. 添加 scope：`connections:write`
4. **Generate** → 复制 token

---

## 第 3 步：配置

```bash
vibe
```

网页打开。粘贴 token。点验证。完事。

---

## 第 4 步：使用

1. 邀请 bot 到频道：`/invite @Vibe Remote`
2. 输入 `/start`
3. 开始写代码

---

## 故障排除

| 问题 | 解决 |
|------|------|
| Bot 不响应 | 检查 `vibe status`，确认 bot 已被邀请 |
| 权限错误 | 重新安装 App 到工作区 |
| Socket 错误 | 确认 `xapp-` token 有 `connections:write` |

日志：`~/.vibe_remote/logs/vibe_remote.log`

诊断：`vibe doctor`
