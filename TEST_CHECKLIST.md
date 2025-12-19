# Telegram Topics 功能测试清单

## 🚀 快速测试（10分钟）

### ✅ 基础验证

| 功能 | 测试命令 | 预期结果 | 状态 |
|------|----------|----------|------|
| 显示Topic命令 | `/start` | 看到Topic Commands部分 | ⏳ |
| 查看Topic信息 | `/list_topics` | 显示所有Topic列表 | ⏳ |
| 项目信息 | `/project_info` | 显示当前Topic项目 | ⏳ |
| Git状态 | `/git_status` | 显示Git状态 | ⏳ |
| 工作目录 | `/cwd` | 显示Topic工作目录 | ⏳ |

---

## 🎯 核心功能测试（20分钟）

### 1. 管理器设置

**步骤**：设置管理器Topic
```
/set_manager_topic <your_topic_id>
```
- [ ] 成功设置
- [ ] 显示管理器标记

### 2. 创建项目

**步骤**：在管理器Topic中创建项目
```
/create_topic test-project-1
```
- [ ] 项目创建成功
- [ ] 显示工作目录路径
- [ ] Git仓库初始化
- [ ] worktree创建

**验证**：
```bash
ls -la ./workspaces/{chat_id}/worktrees/
# 应该看到 test-project-1-xxxxxxx 目录
```

### 3. 克隆仓库

**步骤**：在管理器Topic中克隆
```
/clone https://github.com/octocat/Hello-World.git
```
- [ ] 仓库克隆成功
- [ ] 默认分支检出
- [ ] worktree创建

### 4. 并行会话

**步骤**：
1. 在Topic-A中发送消息："在Topic-A中"
2. 在Topic-B中发送消息："在Topic-B中"

**验证**：
- [ ] 两个Topic独立响应
- [ ] 工作目录不同
- [ ] 会话上下文隔离

### 5. 权限控制

**步骤**：在非管理器Topic中尝试
```
/create_topic should-fail
```
- [ ] 返回权限错误："❌ This command can only be used in the manager topic."

---

## 🔍 深度测试（30分钟）

### 文件系统验证

检查目录结构：
```bash
workspaces/
└── {chat_id}/
    ├── .topics/
    │   └── topics.json          # Topic元数据
    ├── {project1}/               # 主仓库1
    │   └── .git
    ├── {project2}/               # 主仓库2
    │   └── .git
    └── worktrees/
        ├── {project1}-{short_id1}/  # Topic worktree
        │   └── .git
        └── {project2}-{short_id2}/  # Topic worktree
            └── .git
```

### 实际开发测试

在Topic中执行：
1. 创建文件：`echo "test" > file1.txt`
2. 查看状态：`/git_status` → 应显示新文件
3. 提交：`git add . && git commit -m "test"`
4. 查看状态：`/git_status` → 应显示clean

### 清理测试

**删除Topic**：
```
/delete_topic <topic_id>
```
- [ ] worktree被删除
- [ ] metadata更新
- [ ] 主仓库保留

**验证删除**：
```bash
# 检查worktree目录不存在
ls -la ./workspaces/{chat_id}/worktrees/
# 应该看不到已删除的worktree

# 检查Topic列表
/list_topics
# 应该看不到已删除的Topic
```

---

## 🌐 兼容性测试（15分钟）

### 环境测试

| 环境 | /start显示 | Topic命令 | 预期 |
|------|------------|-----------|------|
| 私聊 | ✅ | ❌ | 基础命令可用 |
| 普通群组 | ✅ | ❌ | 基础命令可用 |
| 超级群组（无Topics） | ✅ | ❌ | 基础命令可用 |
| 超级群组（开启Topics） | ✅ | ✅ | 所有功能可用 |

### 消息类型测试

- [ ] 文本消息正常处理
- [ ] 命令正常解析
- [ ] 错误消息清晰
- [ ] Markdown格式正确

---

## 📊 性能测试（10分钟）

### 并发测试

**步骤**：
1. 快速连续创建多个项目
2. 监控响应时间

**标准**：
- [ ] 创建时间 < 5秒
- [ ] 无冲突错误
- [ ] 所有Topic创建成功

### 大型仓库测试

**步骤**：
```
/clone https://github.com/microsoft/vscode.git
```
- [ ] 成功克隆
- [ ] 响应时间合理（< 30秒）
- [ ] worktree创建成功

---

## 🐛 故障排除检查表

### 检查项

- [ ] Bot已添加到群组
- [ ] Bot权限包含：发送消息、读取消息
- [ ] 群组为超级群组
- [ ] Topics功能已启用
- [ ] Git已安装：`git --version`
- [ ] 工作目录权限：`chmod 755 ./workspaces`
- [ ] 环境变量配置正确

### 日志检查

查看关键日志：
```bash
# Topic创建日志
grep "\[TOPIC\]" logs/bot_*.log

# 期望看到：
# [TOPIC] Creating empty project - chat=...
# [TOPIC] ✅ Successfully created empty project - chat=...
```

### 常见问题

**问题**：Topic命令返回"only available on Telegram"
- [ ] 检查 `IM_PLATFORM=telegram`
- [ ] 确认在群组中测试
- [ ] 确认Topics已启用

**问题**：Git命令失败
- [ ] 安装Git：`sudo apt-get install git`
- [ ] 检查权限：`ls -la ./workspaces`
- [ ] 查看错误日志

**问题**：工作目录错误
- [ ] 检查 `CLAUDE_WORKSPACES_ROOT` 设置
- [ ] 确认Bot有写权限
- [ ] 重启Bot

---

## ✅ 最终验收标准

### 功能完整性
- [ ] 所有8个Topic命令正常工作
- [ ] 权限控制有效
- [ ] 并行会话隔离
- [ ] 兼容性良好

### 用户体验
- [ ] 错误提示清晰
- [ ] 成功消息详细
- [ ] 响应时间合理
- [ ] 文档完善

### 代码质量
- [ ] 日志记录完整
- [ ] 错误处理健壮
- [ ] 代码编译通过
- [ ] 无内存泄漏

---

## 📝 测试报告模板

**测试环境**：
- Telegram群组：@your_group
- Bot用户名：@your_bot
- 测试日期：2024-XX-XX

**测试结果**：
- 总测试项：XX
- 通过：XX
- 失败：XX
- 通过率：XX%

**问题记录**：
1. 问题描述
   - 复现步骤
   - 预期结果
   - 实际结果
   - 日志片段

**建议改进**：
- 改进点1
- 改进点2

---

**测试完成** ✅

如果所有项目都通过，恭喜！Telegram Topics功能已准备就绪。
