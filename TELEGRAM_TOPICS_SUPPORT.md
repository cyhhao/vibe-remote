# Telegram Group Topics 支持方案

## 概述

本方案旨在为Telegram Bot添加对Group Topics（群组话题）和Discussion Threads的支持，使其具备与Slack类似的并行会话功能。

## 背景

### 当前限制
- Telegram只支持基于 `chat_id` 的会话管理
- 无法在同一群组中创建多个独立的对话上下文
- 所有消息共享同一个Claude会话
- 无法在同一群组中维护多个独立项目

### 目标能力
- 支持Group Topics中的并行会话
- 每个话题维护独立的Claude会话
- **每个话题对应一个独立的git worktree**
- **主Topic作为管理中心，管理所有项目的生命周期**
- 兼容现有非话题群组和私聊

## 核心特性：Topic-Worktree模式

### 设计理念

**每个Topic = 一个独立项目 = 一个git worktree**

```
超级群组 (项目集)
├── 主Topic (控制台)
│   ├── 创建新项目 topic
│   ├── 列出所有topic
│   ├── git clone操作
│   ├── 创建空项目
│   └── 项目管理命令
│
├── Topic-123 (项目A)
│   ├── /projectA代码开发
│   ├── 运行测试
│   ├── Git操作
│   └── 独立的工作目录
│
├── Topic-456 (项目B)
│   ├── /projectB代码开发
│   ├── 独立的工作目录
│   └── 独立的Claude会话
│
└── Topic-789 (项目C)
    ├── 各种代码任务
    └── 独立的工作目录
```

### 主Topic识别机制

**方案1: 固定Topic ID**
- 在 `.env` 中配置 `TELEGRAM_MANAGER_TOPIC_ID`
- 主Topic ID固定，不允许修改
- 简单直接，但不够灵活

**方案2: 自动识别**
- 第一个使用 `/start` 的Topic自动成为主Topic
- 后续其他Topic创建时在主Topic中通知
- 灵活但可能意外切换

**方案3: 命名约定**
- Topic名称包含 `[MANAGER]` 或 `🔥` 前缀
- 通过Topic标题自动识别主Topic
- 用户友好，可动态创建

**推荐**: 方案1 + 方案3混合
- `.env` 配置主Topic ID作为权威来源
- Topic标题自动标记为 `[MANAGER]` 方便识别
- 提供 `/set_manager_topic` 命令动态切换

## API支持分析

### Telegram Bot API

Telegram Bot API在发送消息时支持 `message_thread_id` 参数：

```python
await bot.send_message(
    chat_id=chat_id,
    text=text,
    message_thread_id=thread_id,  # 话题ID
    parse_mode="MarkdownV2"
)
```

**适用场景**：
- Supergroups（超级群组）中启用了Topics功能
- 消息会发送到指定话题中
- 不同话题间完全隔离

## 数据结构设计

### 1. Topic-Worktree映射

在 `UserSettings` 中添加新的字段：

```python
@dataclass
class UserSettings:
    # 现有字段...
    custom_cwd: Optional[str] = None
    session_mappings: Dict[str, Dict[str, Dict[str, str]]] = field(default_factory=dict)
    active_slack_threads: Dict[str, Dict[str, float]] = field(default_factory=dict)

    # 新增：Topic-Worktree映射
    topic_worktrees: Dict[str, Dict[str, str]] = field(default_factory=dict)
    # 结构: {chat_id: {topic_id: worktree_path}}

    # 新增：主Topic配置
    manager_topic_id: Optional[str] = None
    # 结构: chat_id -> topic_id
```

### 2. 目录结构设计

```
CLAUDE_WORKSPACES/
└── {chat_id}/
    ├── .topics/                    # Topic元数据
    │   └── topics.json            # topic_id -> project_name映射
    ├── {project_name}/             # Git主仓库
    │   └── .git
    └── worktrees/
        ├── {project_name}-{short_topic_id}/  # 每个topic的worktree
        │   └── .git
        ├── {project_name2}-{short_topic_id2}/
        │   └── .git
        └── ...
```

**示例**:
```
CLAUDE_WORKSPACES/
└── -1001234567890/
    ├── .topics/
    │   └── topics.json  # {"123": "my-website", "456": "api-server"}
    ├── my-website/             # 主仓库
    │   └── .git
    └── worktrees/
        ├── my-website-abc123/   # Topic-123的worktree
        │   └── .git
        └── my-website-def456/   # Topic-456的worktree
            └── .git
```

## 命令设计

### 主Topic命令（管理员使用）

| 命令 | 描述 | 示例 |
|------|------|------|
| `/create_topic <name>` | 创建新项目Topic | `/create_topic my-api` |
| `/clone <git_url>` | 克隆项目并创建Topic | `/clone https://github.com/user/repo.git` |
| `/list_topics` | 列出所有Topic | `/list_topics` |
| `/show_topic <topic_id>` | 显示Topic详情 | `/show_topic 123` |
| `/set_manager_topic <topic_id>` | 设置主Topic | `/set_manager_topic 123` |
| `/delete_topic <topic_id>` | 删除Topic（在任意话题直接输入 `/delete_topic` 会弹出确认，删除当前话题及其 worktree） | `/delete_topic 123` |
| `/rename_topic <topic_id> <new_name>` | 重命名Topic | `/rename_topic 123 new-name` |

当管理员在 Telegram 内直接删除某个 forum topic 时，Bot 会监听删除事件并自动清理本地 topics.json 记录与对应的 worktree（同时移除 manager topic 绑定），无需额外执行 `/delete_topic`。

### 项目Topic命令（开发使用）

| 命令 | 描述 | 示例 |
|------|------|------|
| `/project_info` | 显示当前项目信息 | `/project_info` |
| `/open_pr <branch>` | 创建PR | `/open_pr feature/new-ui` |
| `/run_test` | 运行测试 | `/run_test` |
| `/git_status` | 显示Git状态 | `/git_status` |
| `/switch_branch <branch>` | 切换分支 | `/switch_branch main` |

## 工作流程设计

### 流程1: 创建新项目

```
用户 (主Topic)
    ↓
/create_topic my-awesome-project
    ↓
Bot:
1. 检查权限 (仅主Topic)
2. 创建工作目录结构
3. 初始化Git仓库
4. 创建Topic
5. 在主Topic回复: ✅ 已创建Topic-123: my-awesome-project
```

### 流程2: 克隆现有项目

```
用户 (主Topic)
    ↓
/clone https://github.com/user/repo.git
    ↓
Bot:
1. 克隆到 {chat_id}/{repo_name}/
2. 从主仓库创建worktree到 worktrees/{repo_name}-{topic_id}/
3. 创建Topic并关联worktree
4. 在主Topic回复: ✅ 已克隆，Topic-456: repo
```

### 流程3: 开发者在Topic中工作

```
开发者 (项目Topic-123)
    ↓
创建一个新功能
    ↓
Bot:
1. 识别topic_id = 123
2. 查找对应worktree路径
3. 在该worktree中执行所有操作
4. Claude会话关联到该worktree
```

## 架构设计

### 1. Session ID 生成策略

**当前**（仅基于chat_id）：
```
telegram_{channel_id}
```

**新方案**（基于chat_id + thread_id）：
```
# 无话题的聊天（私聊/普通群组）
telegram_{channel_id}

# 有话题的群组
telegram_{channel_id}_{thread_id}
```

### 2. MessageContext 流程

```
用户发送消息
    ↓
检查是否为Group Topic
    ↓
设置MessageContext.thread_id
    ↓
生成Session ID (包含thread_id)
    ↓
使用对应Claude会话
```

### 3. 兼容性设计

| 场景 | thread_id | session_id格式 | 说明 |
|------|-----------|----------------|------|
| 私聊 | None | `telegram_{chat_id}` | 与现有逻辑相同 |
| 普通群组 | None | `telegram_{chat_id}` | 与现有逻辑相同 |
| 超级群组话题 | topic_id | `telegram_{chat_id}_{topic_id}` | 新功能，独立会话 |

## 实施方案

### 阶段1: 修改Telegram客户端

#### 1.1 更新 `should_use_thread_for_reply()`

**文件**: `modules/im/telegram.py`

```python
def should_use_thread_for_reply(self) -> bool:
    """Telegram supports Group Topics (message_thread_id)"""
    return True
```

#### 1.2 更新 `send_message()` 方法

**添加**:
```python
async def send_message(
    self,
    context: MessageContext,
    text: str,
    parse_mode: Optional[str] = None,
    reply_to: Optional[str] = None,
) -> str:
    """Send a text message with topic support"""
    bot = self.application.bot

    chat_id = int(context.channel_id)
    markdownv2_text = self._convert_to_markdownv2(text)
    kwargs = {
        "chat_id": chat_id,
        "text": markdownv2_text,
        "parse_mode": "MarkdownV2"
    }

    # 支持Group Topics
    if context.thread_id:
        kwargs["message_thread_id"] = int(context.thread_id)

    # 回复支持
    if reply_to:
        kwargs["reply_to_message_id"] = int(reply_to)

    try:
        message = await bot.send_message(**kwargs)
        return str(message.message_id)
    except TelegramError as e:
        logger.error(f"Error sending message: {e}")
        raise
```

#### 1.3 更新 `send_message_with_buttons()` 方法

**添加**同样的 `message_thread_id` 支持。

### 阶段2: 更新Session管理

#### 2.1 修改 `SessionHandler.get_base_session_id()`

**文件**: `core/handlers/session_handler.py`

```python
def get_base_session_id(self, context: MessageContext) -> str:
    """Get base session ID with topic support"""
    if self.config.platform == "telegram":
        # 支持话题的session ID
        if context.thread_id:
            return f"telegram_{context.channel_id}_{context.thread_id}"
        # 无话题的聊天
        return f"telegram_{context.channel_id}"
    elif self.config.platform == "slack":
        return f"slack_{context.thread_id}"
    else:
        return f"{self.config.platform}_{context.user_id}"
```

#### 2.2 更新Message处理逻辑

**文件**: `modules/im/telegram.py` - `handle_telegram_message()`

```python
async def handle_telegram_message(self, update: Update, tg_context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming text messages with topic support"""
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type

    # 授权检查
    if not self._is_authorized_chat(chat_id, chat_type):
        logger.info(f"Unauthorized message from chat: {chat_id}")
        await self._send_unauthorized_message(chat_id)
        return

    # 检查是否为话题消息
    thread_id = None
    if hasattr(update.message, 'message_thread_id') and update.message.message_thread_id:
        thread_id = str(update.message.message_thread_id)
        logger.info(f"Message from topic: {thread_id}")

    # 创建MessageContext（包含thread_id）
    context = MessageContext(
        user_id=str(update.effective_user.id),
        channel_id=str(chat_id),
        message_id=str(update.message.message_id),
        thread_id=thread_id,  # 新增：支持话题
        platform_specific={"update": update, "tg_context": tg_context},
    )

    # 处理命令或消息
    message_text = update.message.text
    if message_text.startswith("/"):
        parts = message_text.split(maxsplit=1)
        command = parts[0][1:]
        args = parts[1] if len(parts) > 1 else ""

        if command in self.on_command_callbacks:
            await self.on_command_callbacks[command](context, args)
    elif self.on_message_callback:
        await self.on_message_callback(context, message_text)
```

#### 2.3 更新Callback处理逻辑

**文件**: `modules/im/telegram.py` - `handle_telegram_callback()`

```python
async def handle_telegram_callback(self, update: Update, tg_context: ContextTypes.DEFAULT_TYPE):
    """Handle callback queries with topic support"""
    query = update.callback_query
    chat_id = query.message.chat_id
    chat_type = query.message.chat.type

    logger.info(f"Telegram callback: data='{query.data}', chat={chat_id}")

    # 授权检查
    if not self._is_authorized_chat(chat_id, chat_type):
        await query.answer("❌ This chat is not authorized.", show_alert=True)
        return

    # 检查话题
    thread_id = None
    if hasattr(query.message, 'message_thread_id') and query.message.message_thread_id:
        thread_id = str(query.message.message_thread_id)

    # 创建MessageContext
    context = MessageContext(
        user_id=str(query.from_user.id),
        channel_id=str(chat_id),
        message_id=str(query.message.message_id),
        thread_id=thread_id,  # 新增：支持话题
        platform_specific={
            "query": query,
            "update": update,
            "tg_context": tg_context,
            "callback_id": query.id,
        },
    )

    if self.on_callback_query_callback:
        await self.on_callback_query_callback(context, query.data)

    await query.answer()
```

### 阶段3: 用户体验优化

#### 3.1 添加话题识别消息

在 `handle_telegram_message()` 中添加：

```python
if thread_id:
    logger.info(f"Processing message in topic {thread_id} of chat {chat_id}")
else:
    logger.info(f"Processing message in chat {chat_id} (no topic)")
```

#### 3.2 状态显示优化

更新 `/cwd` 等命令的返回信息，显示当前话题信息：

```python
# 在CommandHandlers中
async def handle_cwd(self, context: MessageContext, args: str):
    """Show current working directory with topic info"""
    cwd = self.controller.get_cwd(context)

    # 构建状态消息
    status = f"📁 **Current Directory**\n\n"
    status += f"`{cwd}`\n\n"

    # 显示话题信息
    if context.thread_id:
        status += f"💬 **Topic**: `{context.thread_id}`\n"

    await self.controller.im_client.send_message(context, status)
```

## 测试策略

### 场景1: 私聊（无话题）
```
用户A <-> 机器人
```
**预期**: 创建单一会话，行为与之前一致

### 场景2: 普通群组（无话题）
```
群组G:
  用户A -> 机器人
  用户B -> 机器人
```
**预期**: 群组内共享会话（当前行为）

### 场景3: 超级群组（多个话题）
```
群组G (超级群组):
  话题1: 项目A讨论
    用户A -> 机器人 (会话A1)
    用户B -> 机器人 (会话B1)
  话题2: 项目B讨论
    用户A -> 机器人 (会话A2，独立于A1)
    用户C -> 机器人 (会话C2)
```
**预期**: 每个话题独立会话

### 场景4: 跨话题切换
```
用户在话题1开始对话 -> 切换到话题2 -> 继续对话
```
**预期**: 话题1和话题2的会话完全独立

## 配置要求

### Bot配置
- Bot必须在超级群组中具有发送消息到话题的权限
- 群组需要启用Topics功能

### 兼容性
- 无需修改 `.env` 配置
- 向后兼容现有部署
- 自动检测话题功能

## 日志和监控

### 关键日志点

1. **话题识别**
   ```
   INFO: Message from topic: 123 for chat: -456
   ```

2. **Session创建**
   ```
   INFO: Creating Claude client for telegram_-456_123 at /path
   INFO: Using existing Claude SDK client for telegram_-456_123 at /path
   ```

3. **Session清理**
   ```
   INFO: Cleaned up Claude session telegram_-456_123
   ```

### 监控指标

- 每个话题的会话数量
- 会话创建/销毁频率
- 话题间会话隔离验证

## 风险评估

### 低风险
- ✅ 向后兼容：现有聊天不受影响
- ✅ 可选功能：仅在有话题时启用
- ✅ 渐进式：可逐步迁移

### 注意事项
- ⚠️ 群组管理员需要启用Topics功能
- ⚠️ Bot权限需要包含话题消息发送
- ⚠️ Session数量可能增加（每个话题一个）

## 实施步骤

### 阶段1: 基础Topics支持（2-3天）

#### 步骤1: 修改Telegram客户端
- [ ] 修改 `should_use_thread_for_reply()` 返回 `True`
- [ ] 更新 `send_message()` 支持 `message_thread_id`
- [ ] 更新 `send_message_with_buttons()` 支持话题
- [ ] 更新Session ID生成逻辑 (包含thread_id)

#### 步骤2: 消息处理增强
- [ ] 更新 `handle_telegram_message()` 提取thread_id
- [ ] 更新 `handle_telegram_callback()` 支持话题
- [ ] 测试私聊和普通群组兼容性

### 阶段2: Topic-Worktree管理（3-4天）

#### 步骤3: 数据结构扩展
- [ ] 扩展 `UserSettings` 添加 `topic_worktrees` 字段
- [ ] 扩展 `UserSettings` 添加 `manager_topic_id` 字段
- [ ] 更新 `_load_settings()` 和 `_save_settings()` 方法
- [ ] 添加Topic-Worktree管理辅助方法

#### 步骤4: 创建TopicManager模块
- [ ] 创建 `modules/topic_manager.py`
- [ ] 实现 `create_empty_project()` - 创建空项目
- [ ] 实现 `clone_project()` - 克隆Git仓库
- [ ] 实现 `list_topics()` - 列出所有Topic
- [ ] 实现 `get_worktree_for_topic()` - 获取Topic工作目录
- [ ] 实现 `delete_topic()` - 删除Topic

#### 步骤5: 工作目录集成
- [ ] 更新 `SessionHandler.get_working_path()` 支持Topic-Worktree
- [ ] 当有Topic时，使用worktree路径而非custom_cwd
- [ ] 集成Git worktree命令 (git worktree add/remove)

### 阶段3: 管理命令（2-3天）

#### 步骤6: 主Topic管理命令
- [ ] 在 `CommandHandlers` 中添加 `/create_topic` 命令
- [ ] 在 `CommandHandlers` 中添加 `/clone` 命令
- [ ] 在 `CommandHandlers` 中添加 `/list_topics` 命令
- [ ] 在 `CommandHandlers` 中添加 `/show_topic` 命令
- [ ] 在 `CommandHandlers` 中添加 `/set_manager_topic` 命令
- [ ] 在 `CommandHandlers` 中添加 `/delete_topic` 命令
- [ ] 在 `CommandHandlers` 中添加 `/rename_topic` 命令

#### 步骤7: 项目Topic命令
- [ ] 添加 `/project_info` - 显示当前项目信息
- [ ] 添加 `/git_status` - 显示Git状态
- [ ] 添加 `/switch_branch` - 切换分支
- [ ] 添加 `/run_test` - 运行测试
- [ ] 添加 `/open_pr` - 创建PR

### 阶段4: 权限与安全（1天）

#### 步骤8: 权限控制
- [ ] 验证主Topic身份 (检查 `context.thread_id == manager_topic_id`)
- [ ] 限制管理命令仅在主Topic使用
- [ ] 添加权限检查装饰器

#### 步骤9: 安全措施
- [ ] 验证Git URL安全性 (避免命令注入)
- [ ] 验证路径安全性 (防止目录遍历)
- [ ] 清理临时文件

### 阶段5: 测试与优化（2天）

#### 步骤10: 功能测试
- [ ] 测试Topic创建和管理流程
- [ ] 测试Git worktree功能
- [ ] 测试并行Topic会话隔离
- [ ] 测试权限控制
- [ ] 压力测试 (多个Topic同时工作)

#### 步骤11: 优化
- [ ] 添加Topic标识到状态消息
- [ ] 完善日志记录 (Topic创建/删除/切换)
- [ ] 添加进度指示 (克隆大型仓库)
- [ ] 性能优化 (并发Topic处理)

**总计**: 10-13天

## 配置更新

### 新增环境变量

在 `.env` 中添加：

```bash
# Telegram Topics支持
TELEGRAM_MANAGER_TOPIC_ID=123  # 主Topic的ID (可选)

# 工作空间根目录
CLAUDE_WORKSPACES_ROOT=/path/to/workspaces  # 默认: ./workspaces

# 允许的Git域名 (安全用)
ALLOWED_GIT_DOMAINS=github.com,gitlab.com,bitbucket.org
```

### 目录权限要求

```bash
# 确保Bot有权限访问工作目录
chmod 755 /path/to/workspaces
chown bot_user:bot_group /path/to/workspaces
```

## 预期收益

1. **功能对齐**: Telegram与Slack功能对等
2. **用户体验**: 支持多项目并行讨论
3. **组织效率**: 大型群组中更好的会话管理
4. **可扩展性**: 为未来Telegram新功能奠定基础

## 参考资料

- [Telegram Bot API - sendMessage](https://core.telegram.org/bots/api#sendmessage)
- [Telegram Topics Documentation](https://telegram.org/blog/topics-in-groups-channels)
- [Current Slack Thread Implementation](../modules/im/slack.py)
