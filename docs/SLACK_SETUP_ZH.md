# Slack 配置指南（5 分钟）

## 太长不看

1. 运行 `vibe` → 浏览器打开设置向导
2. 跟着向导创建 Slack App 并获取 token
3. 完事！

---

## 第 1 步：启动设置向导

```bash
vibe
```

浏览器会自动打开设置向导：

![设置向导欢迎页](../assets/screenshots/setup-welcome-zh.png)

点击 **开始设置** 开始。

---

## 第 2 步：创建 Slack App

向导会引导你创建预配置好权限的 Slack App：

![Slack 配置](../assets/screenshots/setup-slack-zh.png)

1. 点击 **创建 Slack 应用** — 打开 Slack 并自动填充配置
2. 选择你的工作区，点击 **Create**
3. 按照步骤获取 **Bot Token**（`xoxb-`）和 **App Token**（`xapp-`）
4. 粘贴到向导中，点击 **验证令牌**

<details>
<summary><b>手动配置（如需要）</b></summary>

打开 [api.slack.com/apps](https://api.slack.com/apps)，用以下 manifest 创建 App：

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
</details>

---

## 第 3 步：完成并启动

确认配置，点击 **完成并启动**：

![完成设置](../assets/screenshots/setup-finish-zh.png)

向导会显示快速上手提示。

---

## 第 4 步：仪表盘

设置完成后，你会看到仪表盘：

![仪表盘](../assets/screenshots/dashboard-zh.png)

在这里你可以：
- 启动/停止服务
- 配置消息处理选项
- 管理频道设置
- 查看日志和诊断信息

---

## 第 5 步：在 Slack 中使用

1. 邀请 bot 到频道：`/invite @Vibe Remote`
2. 输入 `/start` 或 `@Vibe Remote`
3. 开始写代码！

---

## 故障排除

| 问题 | 解决 |
|------|------|
| Bot 不响应 | 检查 `vibe status`，确认 bot 已被邀请 |
| 权限错误 | 重新安装 App 到工作区 |
| Socket 错误 | 确认 `xapp-` token 有 `connections:write` |

日志：`~/.vibe_remote/logs/vibe_remote.log`

诊断：`vibe doctor`
