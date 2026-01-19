<div align="center">

<img src="assets/logo.png" alt="Vibe Remote" width="120"/>

# Vibe Remote

### 你的 AI 编码军团，用 Slack 指挥。

**不用笔记本电脑。不用 IDE。只需 vibe。**

[![GitHub Stars](https://img.shields.io/github/stars/cyhhao/vibe-remote?color=ffcb47&labelColor=black&style=flat-square)](https://github.com/cyhhao/vibe-remote/stargazers)
[![Python](https://img.shields.io/badge/python-3.9%2B-3776AB?labelColor=black&style=flat-square)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green?labelColor=black&style=flat-square)](LICENSE)

[English](README.md) | [中文](README_ZH.md)

---

![Banner](assets/banner.jpg)

</div>

## 为什么

你在海边。手机响了 — 线上炸了。

**以前的你：** 慌了。找 WiFi。开电脑。等 IDE 加载。晒伤了。

**用了 Vibe Remote：** 打开 Slack。输入「修一下 login.py 的认证 bug」。看着 Claude Code 实时修复。批准。继续喝玛格丽塔。

```
就这样。这就是产品。
```

---

## 10 秒安装

```bash
curl -fsSL https://raw.githubusercontent.com/cyhhao/vibe-remote/master/install.sh | bash && vibe
```

完事。浏览器打开 → 跟着向导走 → 搞定。

---

## 快速开始

### 1. 安装

```bash
curl -fsSL https://raw.githubusercontent.com/cyhhao/vibe-remote/master/install.sh | bash
```

<details>
<summary><b>Windows？</b></summary>

```powershell
irm https://raw.githubusercontent.com/cyhhao/vibe-remote/master/install.ps1 | iex
```
</details>

### 2. 启动

```bash
vibe
```

浏览器会自动打开设置向导。

### 3. 跟着向导走

Web UI 会引导你创建 Slack App、获取 token、配置 Agent — 全在一个页面搞定。

查看[详细配置指南](docs/SLACK_SETUP_ZH.md)（含截图）。

### 4. 开始 Vibe

设置完成后，你会看到一个仪表盘来管理一切：

![仪表盘](assets/screenshots/dashboard-zh.png)

在 Slack 里输入 `/start` 或 `@Vibe Remote`。你的 AI 编码助手准备好了。

---

## 命令

| Slack 里 | 干嘛的 |
|----------|--------|
| `/start` | 打开控制面板 |
| `/stop` | 停止当前会话 |
| 直接打字 | 跟 Agent 对话 |
| 在线程里回复 | 继续对话 |

**技巧：** 每个 Slack 线程 = 独立会话。开多个线程可以并行任务。

---

## 即时切换 Agent

对话中途想换个 Agent？加个前缀就行：

```
Plan: 设计一个新的 API 缓存层
```

就这样。不用菜单，不用命令。输入 `AgentName:` 消息就自动路由到对应 Agent。

```
Code-Reviewer: 检查我刚开的 PR
Build: 编译并运行测试
```

支持 OpenCode agents 和 Claude Code 自定义 agents。大小写不敏感。支持中英文冒号。

---

## 按频道路由

不同项目，不同 Agent：

```
#frontend    → OpenCode（快速迭代）
#backend     → Claude Code（复杂逻辑）
#prototypes  → Codex（快速实验）
```

在 Web UI → Channels 配置。

---

## CLI

```bash
vibe          # 启动一切
vibe status   # 检查运行状态
vibe stop     # 停止一切
vibe doctor   # 诊断问题
```

---

## 前置条件

你需要至少安装一个编码 Agent：

<details>
<summary><b>Claude Code</b>（推荐）</summary>

```bash
npm install -g @anthropic-ai/claude-code
```
</details>

<details>
<summary><b>OpenCode</b></summary>

```bash
curl -fsSL https://opencode.ai/install | bash
```
</details>

<details>
<summary><b>Codex</b></summary>

```bash
npm install -g @openai/codex
```
</details>

---

## 安全

- **本地优先** — Vibe Remote 跑在你机器上
- **Socket Mode** — 没有公开 URL，没有 webhook
- **你的 token** — 存在 `~/.vibe_remote/`，永不上传
- **你的代码** — 留在你硬盘，只发给你选的 AI 提供商

---

## 卸载

```bash
vibe stop && uv tool uninstall vibe-remote && rm -rf ~/.vibe_remote
```

---

## 路线图

- [ ] Discord & Teams 支持
- [ ] Slack 文件附件
- [ ] 多工作区
- [ ] 云中继模式（可选）

---

## 文档

- **[Slack 安装指南](docs/SLACK_SETUP_ZH.md)** — 创建你的 Slack App
- **[English Setup Guide](docs/SLACK_SETUP.md)** — English guide

---

<div align="center">

**停止上下文切换。开始 vibe coding。**

[立即安装](#10-秒安装) · [配置 Slack](docs/SLACK_SETUP_ZH.md) · [报告 Bug](https://github.com/cyhhao/vibe-remote/issues)

---

*为随时随地写代码的开发者而建。*

</div>
