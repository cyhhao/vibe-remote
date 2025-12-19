# 📋 快速参考卡

## ⚡ 3分钟上手

### 1️⃣ 配置文件 (.env)
```bash
IM_PLATFORM=telegram
TELEGRAM_BOT_TOKEN=你的Token
TELEGRAM_TARGET_CHAT_ID=-100群组ID
CLAUDE_DEFAULT_CWD=./workspace
```

### 2️⃣ 启动Bot
```bash
./start.sh
```

### 3️⃣ 启用Topics
群组设置 → Topics → 开启

---

## 🎯 核心流程

### 设置管理器
```
/set_manager_topic 123
```

### 创建项目（管理器Topic）
```
/create_topic 项目名
/clone https://github.com/user/repo.git
```

### 开发（项目Topic）
```
任意消息 → Bot在工作目录执行
/project_info  → 查看项目
/git_status    → Git状态
/cwd           → 工作目录
```

---

## 📚 命令速查

### 管理器命令
```
/set_manager_topic <id>  → 设置管理器
/create_topic <name>     → 创建项目
/clone <git_url>         → 克隆仓库
/list_topics             → 列出所有Topic
/show_topic <id>         → 查看Topic详情
/delete_topic <id>       → 删除Topic
```

### 开发命令
```
/project_info            → 项目信息
/git_status              → Git状态
/cwd                     → 工作目录
/clear                   → 重置会话
```

---

## 🗂️ 目录结构
```
workspaces/
└── {群组ID}/
    ├── {项目}/           # 主仓库
    └── worktrees/        # 各Topic工作目录
        └── {项目}-{short_id}/
```

---

## 🆘 快速排错

| 问题 | 解决 |
|------|------|
| Topic命令不工作 | 确认在超级群组+启用Topics |
| Git失败 | `sudo apt-get install git` |
| 权限错误 | 确保Bot在群组中且有权限 |
| 获取群组ID | 访问 `https://api.telegram.org/bot<TOKEN>/getUpdates` |

---

**💡 记住：管理器Topic创建，普通Topic开发！**
