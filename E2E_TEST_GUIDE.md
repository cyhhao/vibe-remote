# Telegram Topics 支持 - E2E 测试指南

## 概述

本指南用于手工测试Telegram Topics功能的完整工作流程。

## 前置要求

### 环境准备

1. **Telegram群组设置**
   - 创建或使用现有的超级群组（Supergroup）
   - 启用Topics功能（群组设置 → Topics → 开启）
   - 创建至少2个Topic（1个作为管理器Topic）

2. **Bot设置**
   - 将Bot添加到群组
   - 授予Bot发送消息权限
   - 确认Bot可以访问Topics

3. **环境变量配置**
   ```bash
   # .env 文件
   IM_PLATFORM=telegram
   TELEGRAM_BOT_TOKEN=your_bot_token
   TELEGRAM_TARGET_CHAT_ID=-100XXXXXXXXX  # 群组ID
   CLAUDE_DEFAULT_CWD=/path/to/default
   CLAUDE_WORKSPACES_ROOT=./workspaces  # 可选
   ```

## 测试场景

### 场景1: 基础Topics支持验证

#### 步骤1: 启动Bot并检查兼容性

**测试命令**：
```
/start
```

**预期结果**：
- ✅ 显示欢迎消息
- ✅ 包含Topic Commands部分
- ✅ 显示以下命令：
  - `/list_topics - List all topics`
  - `/project_info - Show current project`
  - `/git_status - Show git status`
  - `/create_topic <name> - Create new project (manager only)`
  - `/clone <url> - Clone repo (manager only)`

**备注**：即使在非Topic环境中，命令也会显示，但无法执行。

---

### 场景2: Topic管理流程

#### 步骤1: 设置管理器Topic

**前置条件**：已在群组中创建至少1个Topic

**测试命令**（在Topic中执行）：
```
/set_manager_topic <topic_id>
```

**示例**：
```
/set_manager_topic 123
```

**预期结果**：
```
✅ Manager topic set successfully!
🆔 Topic 123: my-manager-topic

💡 Only this topic can use management commands like /create_topic and /clone.
```

#### 步骤2: 创建新项目Topic

**测试命令**（仅限管理器Topic）：
```
/create_topic my-awesome-project
```

**预期结果**：
```
✅ Created new project topic:
📂 Project: my-awesome-project
🆔 Topic ID: 123
📁 Worktree: /workspaces/-100XXXXXXXXX/worktrees/my-awesome-project-123

💡 You can now use this topic for development work.
```

**验证**：
- ✅ 工作目录被创建
- ✅ Git仓库初始化
- ✅ README.md文件存在
- ✅ Git worktree创建成功

#### 步骤3: 列出所有Topic

**测试命令**：
```
/list_topics
```

**预期结果**：
```
📋 Topics List:

• Topic 123: my-awesome-project
• Topic 456: another-project 🔑 (Manager)
```

#### 步骤4: 查看Topic详情

**测试命令**：
```
/show_topic 123
```

**预期结果**：
```
📋 Topic Details
🆔 Topic ID: 123
📂 Project: my-awesome-project
📁 Worktree: /workspaces/.../my-awesome-project-123
```

---

### 场景3: 项目开发流程

#### 步骤1: 查看项目信息

**测试命令**（在项目Topic中）：
```
/project_info
```

**预期结果**：
```
📋 Project Information
🆔 Topic ID: 123
📂 Project: my-awesome-project
📁 Worktree: /workspaces/.../my-awesome-project-123
```

#### 步骤2: 查看Git状态

**测试命令**：
```
/git_status
```

**预期结果**：
```
✅ Git status: Clean (no changes)
```

#### 步骤3: 检查工作目录

**测试命令**：
```
/cwd
```

**预期结果**：
```
📁 Current Working Directory:
`/workspaces/.../my-awesome-project-123`

✅ Directory exists
💬 Topic: 123
🗂️ Using Topic worktree
💡 This is where Agent will execute commands
```

**关键验证**：显示"💬 Topic: 123"和"🗂️ Using Topic worktree"

---

### 场景4: 克隆现有仓库

#### 步骤1: 克隆仓库

**测试命令**（仅限管理器Topic）：
```
/clone https://github.com/user/repo.git
```

**预期结果**：
```
✅ Cloned repository and created topic:
🔗 Repository: https://github.com/user/repo.git
🆔 Topic ID: 456
📁 Worktree: /workspaces/.../repo-456

💡 You can now use this topic for development work.
```

**验证**：
- ✅ 仓库被克隆
- ✅ 默认分支检出
- ✅ Git worktree创建

---

### 场景5: 权限控制测试

#### 步骤1: 尝试在非管理器Topic执行管理命令

**测试命令**（在非管理器Topic中）：
```
/create_topic test-project
```

**预期结果**：
```
❌ This command can only be used in the manager topic.
```

#### 步骤2: 验证普通Topic可以使用普通命令

**测试命令**（在项目Topic中）：
```
/project_info
```

**预期结果**：
```
✅ 显示项目信息
```

---

### 场景6: 并行会话隔离

#### 步骤1: 在Topic-123中发送消息

**操作**：
- 在Topic-123中发送："请创建一个文件test1.txt"
- 在Topic-456中发送："请创建一个文件test2.txt"

**预期验证**：
- ✅ Topic-123的工作目录中只有test1.txt
- ✅ Topic-456的工作目录中只有test2.txt
- ✅ 两个Topic的会话完全独立

#### 步骤2: 检查目录结构

**预期目录结构**：
```
workspaces/
└── -100XXXXXXXXX/
    ├── .topics/
    │   └── topics.json
    ├── my-awesome-project/           # 主仓库
    │   └── .git
    └── worktrees/
        ├── my-awesome-project-123/   # Topic-123的worktree
        │   └── .git
        └── repo-456/                 # Topic-456的worktree
            └── .git
```

---

### 场景7: 清理操作

#### 步骤1: 删除Topic

**测试命令**（仅限管理器Topic）：
```
/delete_topic 123
```

**预期结果**：
```
✅ Deleted topic 123 and its worktree.
```

**验证**：
- ✅ worktree目录被删除
- ✅ metadata更新
- ✅ 主仓库保留

#### 步骤2: 验证Topic列表

**测试命令**：
```
/list_topics
```

**预期结果**：
- ✅ 被删除的Topic不再显示
- ✅ 其他Topic不受影响

---

## 兼容性测试

### 测试1: 私聊环境

**操作**：在私聊中测试命令

**预期结果**：
- ✅ `/start` 显示欢迎消息（但不显示Topic命令）
- ✅ `/cwd` 正常工作
- ✅ 所有基础命令可用
- ✅ Topic命令返回："❌ This command is only available on Telegram with Topics support."

### 测试2: 普通群组（非超级群组）

**操作**：在普通群组中测试

**预期结果**：
- ✅ 所有基础功能正常
- ✅ Topic命令返回："❌ This command is only available on Telegram with Topics support."

### 测试3: 超级群组（未启用Topics）

**操作**：在超级群组中测试

**预期结果**：
- ✅ 所有基础功能正常
- ✅ Topic命令返回："❌ This command is only available on Telegram with Topics support."

---

## 性能测试

### 测试1: 并发Topic创建

**操作**：
- 同时在管理器Topic中创建多个项目
- 监控Bot响应时间

**预期结果**：
- ✅ 所有Topic创建成功
- ✅ 响应时间 < 5秒
- ✅ 无冲突或错误

### 测试2: 大型仓库克隆

**操作**：
```
/clone https://github.com/microsoft/vscode.git
```

**预期结果**：
- ✅ 成功克隆大型仓库
- ✅ 进度信息显示
- ✅ Git worktree创建成功

---

## 故障排除

### 问题1: Topic命令无响应

**可能原因**：
- Bot未添加到群组
- Bot权限不足
- Topics未启用

**解决方案**：
1. 检查Bot是否在群组中
2. 确认Bot有发送消息权限
3. 启用Topics功能

### 问题2: Git命令失败

**可能原因**：
- Git未安装
- 权限不足
- 网络问题

**解决方案**：
1. 安装Git：`sudo apt-get install git`
2. 检查权限：`chmod 755 workspaces`
3. 测试网络连接

### 问题3: 工作目录不存在

**检查命令**：
```
/cwd
```

**解决方案**：
1. 检查CLAUDE_WORKSPACES_ROOT配置
2. 确认Bot有写权限
3. 重启Bot

---

## 测试清单

### 基础功能
- [ ] `/start` 命令显示Topic命令列表
- [ ] `/cwd` 显示Topic信息
- [ ] `/project_info` 显示当前项目
- [ ] `/git_status` 显示Git状态

### 管理功能（管理器Topic）
- [ ] `/set_manager_topic` 设置管理器
- [ ] `/create_topic` 创建新项目
- [ ] `/clone` 克隆仓库
- [ ] `/list_topics` 列出所有Topic
- [ ] `/show_topic` 显示Topic详情
- [ ] `/delete_topic` 删除Topic

### 权限控制
- [ ] 非管理器Topic无法使用管理命令
- [ ] 错误提示清晰
- [ ] 普通命令在所有Topic中可用

### 并行隔离
- [ ] 不同Topic的工作目录独立
- [ ] 会话上下文隔离
- [ ] Git状态独立

### 兼容性
- [ ] 私聊环境正常
- [ ] 普通群组正常
- [ ] 非Topic环境有适当提示

---

## 日志监控

### 关键日志模式

**Topic创建**：
```
[TOPIC] Creating empty project - chat=-100XXXXXXXXX, topic=123, project=my-project
[TOPIC] ✅ Successfully created empty project - chat=-100XXXXXXXXX, topic=123, project=my-project
```

**Topic删除**：
```
[TOPIC] Deleting topic - chat=-100XXXXXXXXX, topic=123
[TOPIC] ✅ Successfully deleted topic - chat=-100XXXXXXXXX, topic=123, project=my-project
```

**会话使用**：
```
INFO: Message from topic: 123 in chat: -100XXXXXXXXX
INFO: Using worktree for topic 123: /workspaces/.../my-project-123
```

---

## 总结

本E2E测试指南涵盖了Telegram Topics功能的完整测试流程。按照本指南进行测试，可以验证所有功能正常工作，并确保用户体验良好。

如遇到问题，请参考故障排除部分，或查看日志文件进行调试。
