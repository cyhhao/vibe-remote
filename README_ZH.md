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

完事。浏览器打开。粘贴 Slack token。搞定。

<details>
<summary><b>Windows？</b></summary>

```powershell
irm https://raw.githubusercontent.com/cyhhao/vibe-remote/master/install.ps1 | iex
```
</details>

---

## 为什么做这个

| 问题 | 解决方案 |
|------|----------|
| Claude Code 很强但需要终端 | Slack 就是你的终端 |
| 上下文切换杀死心流 | 留在一个 App 里 |
| 手机上没法写代码 | 现在可以了 |
| 多个 Agent，多套配置 | 一个 Slack，随便切 |

**支持的 Agent：**
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — 深度推理，复杂重构
- [OpenCode](https://opencode.ai) — 快速、可扩展、社区最爱
- [Codex](https://github.com/openai/codex) — OpenAI 的编码模型

---

## 工作原理

```
┌──────────────┐         ┌──────────────┐         ┌──────────────┐
│      你      │  Slack  │ Vibe Remote  │  stdio  │  AI Agent    │
│   (任何地方)  │ ──────▶ │  (你的 Mac)   │ ──────▶ │  (你的代码)   │
└──────────────┘         └──────────────┘         └──────────────┘
```

1. **你输入**：*「给设置页加个暗黑模式」*
2. **Vibe Remote** 路由到你配置的 Agent
3. **Agent** 读代码、写代码、实时返回
4. **你审查**，在线程里继续迭代

**你的代码不会离开你的机器。** Vibe Remote 本地运行，通过 Slack Socket Mode 连接。

---

## 快速开始

### 1. 安装
```bash
curl -fsSL https://raw.githubusercontent.com/cyhhao/vibe-remote/master/install.sh | bash
```

### 2. 运行
```bash
vibe
```

### 3. 配置 Slack（5 分钟）
Web UI 会引导你完成所有步骤。或者看[详细指南](docs/SLACK_SETUP_ZH.md)。

### 4. 开始 Vibe
```
/start → 选 Agent → 开始输入
```

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
