# 🤖 Vibe Remote - Telegram Topics 快速上手教程

## 前提条件

✅ 你已经创建了Telegram Bot
✅ 你已经创建了超级群组（Supergroup）
✅ Bot已添加到群组并有发送消息权限

---

## 📦 第一步：配置环境

### 1.1 准备配置文件

在项目根目录创建 `.env` 文件：

```bash
# 平台设置
IM_PLATFORM=telegram

# Bot Token（从 @BotFather 获取）
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz

# 你的群组ID（必须以-100开头）
# 获取方法：把Bot拉进群组后，发送一条消息，然后访问：
# https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
TELEGRAM_TARGET_CHAT_ID=-1001234567890

# Claude配置
CLAUDE_PERMISSION_MODE=allow
CLAUDE_DEFAULT_CWD=./workspace
CLAUDE_SYSTEM_PROMPT=You are a helpful AI coding assistant.

# 工作空间根目录（可选）
CLAUDE_WORKSPACES_ROOT=./workspaces
```

### 1.2 启动Bot

```bash
# 激活虚拟环境（如果需要）
source venv/bin/activate

# 启动Bot
./start.sh

# 或直接运行
python main.py
```

---

## 🗂️ 第二步：启用群组Topics

### 2.1 群组设置

1. 进入你的超级群组
2. 点击群组名称进入设置
3. 找到 **"Topics"** 选项
4. 开启 Topics 功能

### 2.2 创建Topics

1. 在群组中，点击底部的 **"New Topic"** 按钮
2. 创建第一个Topic（作为**管理器Topic**）：
   - 名称：`[MANAGER] 项目控制台`
   - 说明：用于管理所有项目的Topic

3. 创建第二个Topic（用于开发）：
   - 名称：`项目A开发`
   - 说明：项目A的代码开发

---

## 🚀 第三步：基本使用流程

### 步骤1：设置管理器Topic

**在管理器Topic中发送**：
```
/set_manager_topic 123
```
（将 `123` 替换为实际的管理器Topic ID）

**Bot回复**：
```
✅ Manager topic set successfully!
🆔 Topic 123: [MANAGER] 项目控制台

💡 Only this topic can use management commands like /create_topic and /clone.
```

### 步骤2：创建第一个项目

**在管理器Topic中发送**：
```
/create_topic my-awesome-project
```

**Bot回复**：
```
✅ Created new project topic:
📂 Project: my-awesome-project
🆔 Topic ID: 456
📁 Worktree: ./workspaces/-1001234567890/worktrees/my-awesome-project-456

💡 You can now use this topic for development work.
```

### 步骤3：开始开发

**切换到项目Topic（Topic-456）**，发送任何消息给Bot，它就会在对应的工作目录中执行。

例如：
```
创建一个Python文件 hello.py，内容输出 "Hello World"
```

**Bot会在 `./workspaces/-1001234567890/worktrees/my-awesome-project-456/` 目录中创建文件！**

---

## 📚 常用命令速查

### 🔑 管理器命令（仅限管理器Topic）

| 命令 | 用途 | 示例 |
|------|------|------|
| `/set_manager_topic <id>` | 设置管理器Topic | `/set_manager_topic 123` |
| `/create_topic <name>` | 创建新项目 | `/create_topic my-api` |
| `/clone <git_url>` | 克隆Git仓库 | `/clone https://github.com/user/repo.git` |
| `/list_topics` | 列出所有Topic | `/list_topics` |
| `/show_topic <id>` | 查看Topic详情 | `/show_topic 456` |
| `/delete_topic <id>` | 删除Topic | `/delete_topic 456` |

### 🛠️ 开发命令（任何项目Topic可用）

| 命令 | 用途 | 示例 |
|------|------|------|
| `/project_info` | 查看当前项目 | `/project_info` |
| `/git_status` | 查看Git状态 | `/git_status` |
| `/cwd` | 查看当前工作目录 | `/cwd` |
| `/clear` | 重置会话 | `/clear` |

---

## 💡 使用示例

### 示例1：创建新项目

**场景**：创建一个新的React项目

1️⃣ **在管理器Topic**：
```
/create_topic my-react-app
```

2️⃣ **Bot创建成功后，切换到新创建的Topic**

3️⃣ **开始开发**：
```
使用create-react-app创建项目结构
```

---

### 示例2：克隆现有仓库

**场景**：克隆一个GitHub项目进行开发

1️⃣ **在管理器Topic**：
```
/clone https://github.com/microsoft/vscode.git
```

2️⃣ **Bot克隆完成后，切换到对应的Topic**

3️⃣ **查看项目**：
```
/project_info
```

4️⃣ **开始开发**：
```
查看项目结构，找到package.json文件
```

---

### 示例3：多项目并行开发

**场景**：同时开发3个项目

1️⃣ **在管理器Topic创建3个项目**：
```
/create_topic frontend
/create_topic backend
/create_topic mobile-app
```

2️⃣ **每个Topic对应一个独立的工作目录**
- `frontend` Topic → `./workspaces/.../frontend-xxx/`
- `backend` Topic → `./workspaces/.../backend-xxx/`
- `mobile-app` Topic → `./workspaces/.../mobile-app-xxx/`

3️⃣ **在不同Topic中并行开发，互不干扰！**

---

## 🎯 最佳实践

### ✅ 推荐工作流

1. **先创建管理器Topic**，所有管理操作都在这里
2. **每个项目一个独立Topic**，清晰隔离
3. **用 `/list_topics` 定期查看所有项目**
4. **用 `/project_info` 确认当前项目环境**
5. **用 `/git_status` 查看代码变更**

### ❌ 避免的做法

- ❌ 不要在普通Topic中使用管理命令
- ❌ 不要在私聊中使用Topic命令
- ❌ 不要删除正在使用的主仓库（只删worktree）

---

## 🆘 常见问题

### Q1：如何获取群组ID？

**方法1**：
1. 把Bot拉进群组
2. 在群组发送一条消息
3. 访问：`https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
4. 找到 `chat.id` 字段（以-100开头）

**方法2**：
- 使用 @userinfobot 机器人，它会显示群组ID

### Q2：Topic命令显示"仅限Telegram"？

**原因**：你可能在私聊或普通群组中测试
**解决**：
1. 确保在超级群组中
2. 确保已启用Topics功能
3. 确保Bot在群组中

### Q3：Git命令失败？

**解决**：
```bash
# 安装Git
sudo apt-get install git

# 检查权限
chmod 755 ./workspaces
```

### Q4：如何查看Bot日志？

```bash
# 实时查看日志
tail -f logs/bot_*.log

# 查看Topic相关日志
grep "\[TOPIC\]" logs/bot_*.log
```

---

## 📁 项目结构

创建Topic后，目录结构如下：

```
workspaces/
└── -1001234567890/          # 你的群组ID
    ├── .topics/             # Topic元数据
    │   └── topics.json
    ├── my-awesome-project/  # Git主仓库
    │   └── .git
    └── worktrees/           # 各Topic的独立工作目录
        ├── my-awesome-project-abc123/  # Topic-456的worktree
        │   └── .git
        └── my-react-app-def456/        # Topic-789的worktree
            └── .git
```

---

## 🎉 恭喜！

你现在已经掌握Vibe Remote的Telegram Topics功能！

### 下一步：

- 📖 阅读完整文档：`TELEGRAM_TOPICS_SUPPORT.md`
- 🧪 运行E2E测试：`E2E_TEST_GUIDE.md`
- ✅ 使用检查清单：`TEST_CHECKLIST.md`

**开始你的多项目并行开发之旅吧！** 🚀
