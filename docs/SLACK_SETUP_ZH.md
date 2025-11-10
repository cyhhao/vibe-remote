# Slack 机器人设置指南

本指南将指导你为 Vibe Remote 设置 Slack 机器人。

## 前置条件

- Slack 工作区的管理员权限
- 已安装 Python 3.6 或更高版本
- 已克隆 Vibe Remote 并安装依赖

## 步骤 1：创建 Slack 应用

1. 访问 [https://api.slack.com/apps](https://api.slack.com/apps)
2. 点击 **"Create New App"** → **"From scratch"**
3. 输入应用名称（例如 "Vibe Remote"）
4. 选择你的工作区
5. 点击 **"Create App"**

## 步骤 2：配置机器人令牌权限范围

1. 在应用设置中，导航到侧边栏的 **"OAuth & Permissions"**
2. 向下滚动到 **"Scopes"** → **"Bot Token Scopes"**
3. 添加以下所有 OAuth 权限范围以避免权限问题：

### 基础权限范围（必需）

- `channels:history` - 查看公共频道中的消息
- `channels:read` - 查看公共频道的基本信息
- `chat:write` - 作为机器人发送消息
- `app_mentions:read` - 查看提及你的机器人的消息
- `users:read` - 查看用户的基本信息
- `commands` - 使用斜杠命令（自动添加）

### 私有频道支持

- `groups:read` - 查看私有频道的基本信息
- `groups:history` - 查看私有频道中的消息
- `groups:write` - 向私有频道发送消息

### 增强功能

- `chat:write.public` - 无需加入即可向频道发送消息
- `chat:write.customize` - 使用自定义用户名和头像发送消息
- `files:read` - 查看频道中共享的文件（用于未来的文件处理）
- `files:write` - 上传文件（用于未来的文件上传支持）
- `reactions:read` - 查看表情反应
- `reactions:write` - 添加表情反应
- `users:read.email` - 查看电子邮件地址（用于增强用户信息）
- `team:read` - 查看团队/工作区信息

**注意**：现在添加所有权限比以后多次重新安装应用要好。未使用的权限不会影响机器人的性能。

### 快速权限检查清单

为确保完整功能，请确保已添加：

- ✅ 所有基础权限范围（6 个权限）
- ✅ 所有私有频道权限（3 个权限）
- ✅ 任何你想要的增强功能（最多 8 个额外权限）

**推荐的权限总数：约 17 个权限**，可实现完整功能。

## 步骤 3：将应用安装到工作区

1. 仍在 **"OAuth & Permissions"** 页面，滚动到顶部
2. 点击 **"Install to Workspace"**
3. 查看权限并点击 **"Allow"**
4. 复制 **Bot User OAuth Token**（以 `xoxb-` 开头）
   - 将其保存为 `.env` 文件中的 `SLACK_BOT_TOKEN`

## 步骤 4：启用 Socket 模式（推荐）

Socket 模式允许你的机器人连接而无需暴露公共 URL。

1. 转到侧边栏的 **"Socket Mode"**
2. 将 **"Enable Socket Mode"** 切换为开启
3. 系统会提示你生成应用级令牌：
   - 令牌名称："Socket Mode Token"
   - 添加权限：`connections:write`
   - 点击 **"Generate"**
4. 复制 **App-Level Token**（以 `xapp-` 开头）
   - 将其保存为 `.env` 文件中的 `SLACK_APP_TOKEN`

## 步骤 5：配置事件订阅

1. 转到侧边栏的 **"Event Subscriptions"**
2. 将 **"Enable Events"** 切换为开启
3. 在 **"Subscribe to bot events"** 下，添加所有这些事件：

### 消息事件

- `message.channels` - 公共频道中的消息
- `message.groups` - 私有频道中的消息
- `app_mention` - 有人提及你的机器人时

### 其他事件（可选但推荐）

- `member_joined_channel` - 机器人加入频道时
- `member_left_channel` - 机器人离开频道时
- `channel_created` - 创建新频道时
- `channel_renamed` - 频道重命名时
- `team_join` - 新成员加入工作区时

4. 点击 **"Save Changes"**

**注意**：现在添加所有事件可以避免以后扩展机器人功能时需要重新配置。

## 步骤 6：配置原生斜杠命令（推荐）

原生 Slack 斜杠命令提供最佳用户体验，带有自动完成和帮助文本。以下是详细的分步指南：

### 6.1 访问斜杠命令配置

1. 导航到 [https://api.slack.com/apps](https://api.slack.com/apps)
2. 登录你的 Slack 账户（如果尚未登录）
3. 在"Your Apps"列表中找到你的 Vibe Remote 应用
4. 点击应用名称进入管理界面
5. 在左侧边栏中，找到 **"Features"** 部分并点击 **"Slash Commands"**
6. 你将看到"Slash Commands"页面，显示任何现有命令

**页面概览**：

- 新应用会显示"You haven't created any slash commands yet"
- 绿色的 **"Create New Command"** 按钮位于右上角
- 现有命令以列表形式显示，带有编辑选项

### 6.2 创建命令

**每个命令的步骤**：

1. 点击 **"Create New Command"** 按钮
2. 填写命令表单（见下方具体配置）
3. 点击 **"Save"** 保存命令
4. 等待确认 - Slack 会显示"Your slash command was saved!"消息
5. 返回命令列表验证创建

**重要说明**：

- 命令必须逐个创建
- 命令名称不能重复或包含空格
- 所有字段都区分大小写

### 6.3 必需的命令

**配置指南**：

- 所有命令的 **Request URL** 留空（我们使用 Socket 模式）
- 为所有命令勾选 **"Escape channels, users, and links"**
- 复制并粘贴下面的配置

---

#### 命令 1：`/start`

```
Command: /start
Request URL: (留空)
Short Description: 打开带有交互按钮的主菜单
Usage Hint: 启动 Vibe Remote 并显示菜单
```

**步骤**：

1. 点击 **"Create New Command"**
2. 填写 **"Command"** 字段：`/start`
3. **"Request URL"** 留空
4. 填写 **"Short Description"**：`打开带有交互按钮的主菜单`
5. 填写 **"Usage Hint"**：`启动 Agent 会话并显示菜单`
6. ✅ 勾选"Escape channels, users, and links"
7. 点击 **"Save"**
8. 返回 Slash Commands 页面

#### 命令 2：`/stop`

```
Command: /stop
Request URL: (留空)
Short Description: 停止 Vibe Remote 会话
Usage Hint: 停止当前机器人会话
```

**步骤**：

1. 点击 **"Create New Command"**
2. 填写 **"Command"** 字段：`/stop`
3. **"Request URL"** 留空
4. 填写 **"Short Description"**：`停止当前 Agent 会话`
5. 填写 **"Usage Hint"**：`停止当前机器人会话`
6. ✅ 勾选"Escape channels, users, and links"
7. 点击 **"Save"**

**✅ 完成**：创建两个命令后，你应该在 Slash Commands 页面看到它们的列表。

### 6.4 重要配置说明

- **Request URL**：使用 Socket 模式时始终留空（推荐）
- **需要 Socket 模式**：这些命令仅在启用 Socket 模式时工作（步骤 4）
- **区分大小写**：命令名称区分大小写
- **无自动化**：目前，Slack 不提供 API 来以编程方式创建斜杠命令 - 必须通过 Web 界面手动创建
- **3 秒响应**：机器人有 3 秒时间确认斜杠命令（自动处理）

### 6.5 验证

创建两个命令后：

1. 返回主"Slash Commands"页面
2. 你应该看到 `/start` 和 `/stop` 命令都列出
3. 每个命令应显示"✅ Configured"状态

### 6.6 设置检查清单

**验证你的配置**：

- [ ] 访问 [https://api.slack.com/apps](https://api.slack.com/apps) 并选择你的应用
- [ ] 导航到"Features" > "Slash Commands"页面
- [ ] 创建 `/start` 命令
- [ ] 创建 `/stop` 命令
- [ ] 所有命令的 **Request URL** 为空
- [ ] 所有命令都勾选了 **"Escape channels, users, and links"**
- [ ] 两个命令都出现在 Slash Commands 页面

### 6.7 斜杠命令故障排除

如果命令不在自动完成中显示：

- 通过"OAuth & Permissions"页面重新安装应用到工作区
- 验证 Socket 模式已启用
- 确保机器人被邀请到频道：`/invite @YourBotName`

如果命令执行但无响应：

- 检查机器人进程正在运行
- 验证 `SLACK_APP_TOKEN` 和 `SLACK_BOT_TOKEN` 配置正确

## 步骤 7：配置你的环境

使用以下内容更新你的 `.env` 文件：

```env
# 设置平台为 slack
IM_PLATFORM=slack

# Slack 配置
SLACK_BOT_TOKEN=xoxb-your-bot-token-here
SLACK_APP_TOKEN=xapp-your-app-token-here

# 可选：允许的频道 ID 白名单（null = 所有频道）
SLACK_TARGET_CHANNEL=[C1234567890,C0987654321]
```

### 查找频道 ID

要查找频道 ID：

1. 在 Slack 中右键点击频道名称
2. 选择 **"View channel details"**
3. 滚动到底部
4. 公共频道的频道 ID 以 `C` 开头

## 步骤 8：邀请机器人到频道

在机器人可以与频道交互之前，你需要邀请它：

1. 在 Slack 中，转到频道
2. 输入 `/invite @YourBotName`
3. 按 Enter

## 步骤 9：启动机器人

```bash
python main.py
```

## 使用方法

### 命令方法

机器人支持多种交互方式：

#### 1. 斜杠命令（推荐）

```
/start
/stop
```

- 提供自动完成和帮助文本
- 在机器人被邀请的任何频道中工作
- 最友好的用户体验

#### 2. 在频道中

- 使用 `/start` 开始
- 机器人创建线程来组织对话
- 所有响应都出现在线程中

### 使用机器人

#### 主要命令

- `/start` - 打开带有交互按钮的主菜单：
  - **Current Dir** - 显示当前工作目录
  - **Change Work Dir** - 打开模态框更改工作目录
  - **Reset Session** - 清除对话上下文并重新开始
  - **Settings** - 配置消息可见性偏好
  - **How it Works** - 显示帮助信息

#### 附加命令

- `/stop` - 停止当前 Vibe Remote 会话

#### 向 Agent 发送消息

使用 `/start` 后，只需在频道中输入你的消息。机器人将：

1. 将你的消息发送到已配置的 Agent
2. 实时流式传输响应
3. 为后续消息维护对话上下文

## 线程支持

Slack 机器人自动使用线程来组织对话：

- 每个用户的消息都分组在一个线程中
- Agent 的响应出现在同一线程中
- 这使频道历史记录保持整洁有序

## 故障排除

### 机器人无响应

1. 检查机器人是否在线（Slack 中的绿点）
2. 验证机器人是否被邀请到频道
3. 检查日志中的任何错误消息

### 权限错误

1. 确保添加了所有必需的权限范围（包括私有频道的 `groups:read`）
2. **重要**：添加新权限范围后，你必须重新安装应用到工作区：
   - 转到 **"OAuth & Permissions"** 页面
   - 点击顶部的 **"Reinstall to Workspace"** 按钮
   - 查看并批准新权限
3. 验证令牌在 `.env` 中正确设置

### 私有频道访问问题

如果你看到 `missing_scope` 错误与 `groups:read`：

1. 添加所有私有频道权限范围：`groups:read`、`groups:history`、`groups:write`
2. 点击 **"Reinstall to Workspace"**（这是强制性的！）
3. 复制新的 Bot Token 并更新你的 `.env` 文件
4. 重启机器人
5. 确保机器人被邀请到私有频道：`/invite @YourBotName`

### 线程回复问题

如果你看到 `cannot_reply_to_message` 错误：

1. 这通常意味着机器人正在尝试回复不存在的消息或在错误的上下文中
2. 确保机器人对频道类型有 `channels:history` 或 `groups:history` 权限
3. 检查消息时间戳（thread_ts）是否有效
4. 验证机器人是它尝试回复的频道的成员

### Socket 模式问题

1. 确保 `SLACK_APP_TOKEN` 设置正确
2. 检查应用级令牌是否有 `connections:write` 权限范围
3. 验证应用设置中已启用 Socket 模式

### 斜杠命令问题

如果斜杠命令不工作：

1. 验证命令在应用设置中已创建
2. 检查 Socket 模式已启用
3. 确保机器人被邀请到频道：`/invite @YourBotName`
4. 如需要重启机器人

检查机器人日志的启动确认：

```
INFO - Starting Slack bot in Socket Mode...
INFO - A new session has been established
```

## 替代方案：Webhook 模式

如果你更喜欢使用 webhook 而不是 Socket 模式：

1. 设置公共 HTTPS 端点
2. 在事件订阅中配置请求 URL
3. 将 `SLACK_SIGNING_SECRET` 添加到你的 `.env`
4. 删除或留空 `SLACK_APP_TOKEN`

注意：Webhook 模式需要公开可访问的 HTTPS 端点，设置更复杂。

## 安全注意事项

- 永远不要将令牌提交到版本控制
- 对所有敏感数据使用环境变量
- 定期轮换你的令牌
- 仅向机器人授予必要的权限

## 其他资源

- [Slack API 文档](https://api.slack.com/)
- [Python Slack SDK](https://slack.dev/python-slack-sdk/)
- [Socket 模式指南](https://api.slack.com/apis/connections/socket)
