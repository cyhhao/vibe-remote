<div align="center">

<img src="assets/logo.png" alt="Vibe Remote" width="120"/>

# Vibe Remote

### 你的 AI agent 军团，用 Slack、Discord、Telegram、微信或飞书指挥。

**不用笔记本电脑。不用 IDE。只需 vibe。**

[![GitHub Stars](https://img.shields.io/github/stars/cyhhao/vibe-remote?color=ffcb47&labelColor=black&style=flat-square)](https://github.com/cyhhao/vibe-remote/stargazers)
[![Python](https://img.shields.io/badge/python-3.9%2B-3776AB?labelColor=black&style=flat-square)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green?labelColor=black&style=flat-square)](LICENSE)

<a href="https://www.producthunt.com/products/vibe-remote?embed=true&utm_source=badge-featured&utm_medium=badge&utm_campaign=badge-vibe-remote" target="_blank" rel="noopener noreferrer"><img alt="Vibe Remote - Code from your phone — AI agents in your chat app | Product Hunt" width="250" height="54" src="https://api.producthunt.com/widgets/embed-image/v1/featured.svg?post_id=1104967&theme=light&t=1774450119248"></a>

[English](README.md) | [中文](README_ZH.md)

**支持的平台**

![Slack](https://img.shields.io/badge/Slack-4A154B?style=flat-square&logo=data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACMAAAAiCAYAAADVhWD8AAACT0lEQVR42s2VQW7bMBBFfQQdoDa47CIiBBSuu/QRvCpid1HdwNrHjnUDH4Ww0irdCWjtZukjMF0ViGOM0wuoZJRkCFkUJZmIMsAHLXNAPo5Gn506Mdg8TD9uDlwofRxv/n3N59Afl1Maz0EoFeI0vsQcW9Ffgy8h8hr8huAF5Hq2kBB5WQfCihwJnnNkJYphZlvbMKlOXgKOdx2SYwiUbRjQwXy6AfKqMIM1BO3DqNX5BSH2TqswGHJzVfI/6zC4kVkdJZrAEBY6HguJ1tD6a2zSMqHpPSyawNB4sdQYIxpafaHpmWEQpGj+7OpimMFsDtvaICioCuOx0NHNu/E8QUMzyIbpeeL0JTn86TUdkhNguHIdgO46QBhDZT78hFFjGHlzI0yguyhLYbBnsIn761q9w6Urd3Lhfp/7eGHK8SLL0cC48SxBkLca3Bk5nJwTVbqcsnWAEecv80gFFa/zpzeZ3va+wG1vkqqSGys5CzVHPufX2Ud0eR9REEqraBdRvr+iuA7vTnwEOIZBkIL53jjIgaSN9I1mH4I47dYEI35zTQ48wzSBQLlJBoMLF8Jw4jtlOXIN+f5PgRFV5RlMd5yUnVoClcJgZeDkyvB358PifpiEdWDuIho0hdmxs6HSxOORUiGOjVkZJgNirn+/otuKECArgiDmMMNYCOswO+YNVQF7T1qA0X9N+8hdtg6jareiYfswqofYChum17EZYlPNlTHmBtNDQ7MQ6ENFxtj9PDKZ3h3zPJss6NSZMXI5ymecRdOTldivKBcjqwPyH3MZonz6I6ghAAAAAElFTkSuQmCC)
![Discord](https://img.shields.io/badge/Discord-5865F2?style=flat-square&logo=discord&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-26A5E4?style=flat-square&logo=telegram&logoColor=white)
![WeChat](https://img.shields.io/badge/%E5%BE%AE%E4%BF%A1-07C160?style=flat-square&logo=wechat&logoColor=white)
![Lark](https://img.shields.io/badge/%E9%A3%9E%E4%B9%A6%20%2F%20Lark-3370FF?style=flat-square&logo=bytedance&logoColor=white)

**支持的 Agent**

![Claude Code](https://img.shields.io/badge/Claude%20Code-D4A27F?style=flat-square&logo=anthropic&logoColor=white)
![OpenCode](https://img.shields.io/badge/OpenCode-00B4D8?style=flat-square&logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAzMCIgZmlsbD0ibm9uZSI+PHBhdGggZD0iTTE4IDZINlYyNEgxOFY2Wk0yNCAzMEgwVjBIMjRWMzBaIiBmaWxsPSIjZmZmIi8+PC9zdmc+)
![Codex](https://img.shields.io/badge/Codex-412991?style=flat-square&logo=data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAAAXNSR0IArs4c6QAAAERlWElmTU0AKgAAAAgAAYdpAAQAAAABAAAAGgAAAAAAA6ABAAMAAAABAAEAAKACAAQAAAABAAAAIKADAAQAAAABAAAAIAAAAACshmLzAAAEHklEQVRIDe2VWSitbRTHDyGZZ5EhQyJTxgwhKVOZM4QLuSCSqXBDUS5wQZkuSUSZbkw3KKSMhSJCCZmnEJnP7+s9Obt3s/f39XXuzlN7t571rLX+z7PWf61X4ePj48efXIp/Mvg/sf8CyM2wklwLDI6Ojubm5k5PTy0sLPz8/HR1df+N1y8bWCRjvb29VVdX6+npGRoaWltba2trW1patrW1HR4eLi8vb21tPT8/y3Dn6Ifs44KCAgMDg66urqurq/f39/Pz86SkJK6mo6OjoaGhpqbm6OjY2dkpI4gsgJmZGVVV1YWFBcGfW+fm5urr6ycnJ4+Pj+/u7pK3wsJCJSWl8vLy7zC+Bbi5uYmNjU1MTMTz/v6+vr6ep7i5uY2NjYli9fT08CYgRXph+zVAb2+vubk5bg0NDdjl5+draWk1NTW9vLywXVlZCQsLc3BwGBgYEKJERERERUUJsuj/i0br7+9PTU1NSUmxt7enyMDs7OyQlry8vOvr69LS0pCQEJT+/v7p6emRkZEbGxsJCQlra2vAoxcvESDFNDY2LisrQ09C6urqEKKjozMyMtrb201MTPDnsgJ5lpaWQkNDoZaXl5eTkxO3EUVjK05RX18fDpeXl5y5uroKALxGWVnZyMiotraWYpiZmfn6+k5MTAjhcLGzsyOlJycn8gEqKyu5uGAHAOGQ4+Li6K/9/X1BjxAQEMBTAN7e3kZ5cXHh6enJy6QfIa4Bxby7u4Py+L++vpJZoQyACWVHj8AlnJ2d6W0fH5+Wlha429raCsHoPgwklxggMDBwb29vcXERI8gzPDwcFBTENUmRpBv1tLW1JUvwuLGxkSMPDw8rKytaR9IMWQzg7u7OS3Nycs7OzrKysubn53FbX1+nngALzggwFVlBQYFOFrAVFRXpbV4v2Pz+F9Iq+U9ohhqThw4S9FNTUyTd1NS0pqaG0cRcUlFRgbic0ihgIMAL2AHFJUMhi1mEiqTDOW9vbwZOcHAw8wAlvORl3IsCMJoyMzPhLnoAKAZCSUkJN6BRkCWXOEWEoMLMhuzsbPJDzcGgCcLDw0GqqKhYXV1NS0sjIVAAY4THx8fi4mKQaHXu9Ds5giSJ9inHxMTQosJ2ZGQEzjCX6Fg0T09PxNLU1CwqKmLb3NxMHOo0ODj46S4pfJEijicnJ7lad3e3pCny0NAQCaHVuezDwwMaWiE+Pp7aiiw/t18DcFxVVQU9mM/T09Obm5ujo6MEYqDC3ePjY8GfSc4nYXZ29jOctPAtAKawyMXFRV1dHf4Jn0mGGvOAJuDL09HRAR7Zlw4qqZEFgB2VZJRSWOJCHhsbG8rOV5PWhaywVno2SEZHVuAnrvv3+9vbW7h0cHDA4IPHFON7218n/w1Abjhpgy/6QNro/2j+AsjN3k91TuJWs4eHugAAAABJRU5ErkJggg==)

---

![Banner](assets/banner.jpg)

</div>

## 为什么

你在海边。手机响了 — 线上炸了。

**以前的你：** 慌了。找 WiFi。开电脑。等 IDE 加载。晒伤了。

**用了 Vibe Remote：** 打开 Slack、Discord、Telegram 或微信。输入「修一下 login.py 的认证 bug」。看着 Claude Code 实时修复。批准。继续喝玛格丽塔。

```
让 AI 去忙，你去浪。
```

---

## 10 秒安装

```bash
curl -fsSL https://raw.githubusercontent.com/cyhhao/vibe-remote/master/install.sh | bash && vibe
```

完事。浏览器打开 -> 跟着向导走 -> 搞定。

如果你希望从另一台设备或远端服务器打开 Web UI，运行：

```bash
vibe remote
```

<details>
<summary><b>Windows？</b></summary>

Windows 下默认推荐使用 WSL 方案，因为它的兼容性最好。

- 推荐阅读：[Windows 用户：从零开始用 WSL 运行 Vibe Remote](docs/WINDOWS_WSL_ZH.md)

如果你完全没用过 WSL，这篇文档会讲清楚：
- 去哪里安装 WSL
- 应该打开哪个终端窗口
- 在哪里执行 Vibe Remote 的安装命令
- 如何启动 Ubuntu 并打开 Web UI
</details>

---

## 为什么要用

| 问题 | 解决 |
|------|------|
| Claude Code 很强但需要终端 | Slack/Discord/Telegram/微信/飞书就是你的终端 |
| 上下文切换太累 | 一个 App 搞定 |
| 电脑上开工，手机上接着做 | 直接恢复当前项目里的真实 Agent 会话 |
| 手机上写不了代码 | 现在可以了 |
| 多个 Agent，多套配置 | 一个聊天 App，任意 Agent |

**支持的 Agent：**
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — 深度推理，复杂重构
- [OpenCode](https://opencode.ai) — 快速，可扩展，社区最爱
- [Codex](https://github.com/openai/codex) — OpenAI 的编码模型

---

## 为什么选 Vibe Remote 而不是 OpenClaw？

| | Vibe Remote | OpenClaw |
|---|---|---|
| **上手** | 一行命令 + Web 向导，2 分钟搞定。 | Gateway + channels + JSON 配置，准备花一下午。 |
| **安全** | 本地运行，仅 Socket Mode / WebSocket 出站连接。无公开端口，无入站流量，攻击面极小。 | Gateway 暴露端口，组件多，攻击面大。 |
| **Token 成本** | 薄传输层 — 只在 IM 和 Agent 之间转发消息，中间件本身零 LLM 开销。 | 每条消息都带着长长的系统上下文：维持助手人格、IM 工具调用、编排管线等。你的任务还没开始，token 已经在烧了。 |

OpenClaw 是个人 AI 助手 — 闲聊很好用，但它始终运转的 agent 循环让真正的生产力场景成本很高。Vibe Remote 不是 agent 框架，而是一个**遥控器** — 聊天 App 和 AI agent 之间的极简桥梁。不加额外智能层，不加额外 token 开销，不加额外攻击面。每一个 token 都直接花在你的任务上。

---

## 亮点

<table>
<tr>
<td width="33%">

### 设置向导

一行命令安装，引导式配置。不用手动折腾 token。

![设置向导](assets/screenshots/setup-slack-zh.png)

</td>
<td width="33%">

### 仪表盘

实时状态，健康监控，快捷控制。

![仪表盘](assets/screenshots/dashboard-zh.png)

</td>
<td width="33%">

### 频道路由

按频道配置 Agent。不同项目，不同 Agent。

![频道](assets/screenshots/channels-zh.png)

</td>
</tr>
</table>

### 随时随地接收通知

AI 完成任务的那一刻，你就能收到通知。就像老板给员工布置任务一样 — 分配下去，去忙别的，完成了自然会通知你。不用盯着屏幕等。

### Thread = 会话

每个 Slack/Discord/Telegram/微信/飞书会话作用域都是独立工作区。开 5 个会话，跑 5 个并行任务。上下文互不干扰。

### 随时续上会话

电脑一合上，人已经在路上，任务还得继续。

Vibe Remote 可以直接列出当前工作目录下 Claude Code、OpenCode、Codex 的真实历史会话。选中最近那条，立刻从电脑切到手机继续干，不用重新解释背景，不用翻旧 thread，也不用担心上下文断掉。

### 交互式提示

Agent 需要输入时 — 文件选择、确认、选项 — 聊天 App 弹出按钮或模态框。完整 CLI 交互，零终端。

![交互式提示](assets/screenshots/question-zh.jpg)

### 定时任务与异步 Hook

你可以让 Agent 把工作安排到稍后执行、按 cron 周期性触发，或者在长任务结束后异步补发一条消息。

- 用 `vibe task` 保存周期性或一次性提示
- 用 `vibe hook send` 队列化一次性的异步发送
- 需要时可把会话上下文和消息投递目标拆开

---

## 工作原理

```
┌──────────────┐             ┌──────────────┐             ┌──────────────┐
│     你       │   Slack     │              │   stdio     │  Claude Code │
│  (任何地方)  │   Discord   │ Vibe Remote  │ ──────────▶ │  OpenCode    │
│              │   Telegram  │  (你的 Mac)  │ ◀────────── │  Codex       │
│              │   微信      │              │             │              │
│              │   飞书      │              │             │              │
└──────────────┘             └──────────────┘             └──────────────┘
```

1. **你在 Slack/Discord/Telegram/微信/飞书输入**：*"给设置页加个深色模式"*
2. **Vibe Remote** 路由到配置的 Agent
3. **Agent** 读取代码库，写代码，流式返回
4. **你在聊天 App 审查**，在线程里迭代

**你的代码永远不离开你的机器。** Vibe Remote 本地运行，通过 Slack Socket Mode、Discord Gateway、Telegram Bot API 长轮询、微信轮询或飞书 WebSocket 连接。

---

## 命令

| 聊天里 | 干嘛的 |
|----------|--------|
| `@Vibe Remote /start` | 打开控制面板 |
| `/stop` | 停止当前会话 |
| 直接打字 | 跟 Agent 对话 |
| 在线程里回复 | 继续对话 |

**技巧：** 每个线程 = 独立会话。开多个线程可以并行任务。

详细参考：

- [完整命令参考](docs/COMMANDS_ZH.md)
- [CLI 参考手册](docs/CLI_ZH.md)

---

## 即时切换 Agent

对话中途想换个 Agent？加个前缀就行：

```
Plan: 设计一个新的 API 缓存层
```

就这样。不用菜单，不用命令。输入 `AgentName:` 消息就自动路由到对应 Agent。

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
vibe task     # 创建和管理定时任务
vibe hook     # 队列化一次性异步发送
```

详细参考：

- [完整命令参考](docs/COMMANDS_ZH.md)
- [CLI 参考手册](docs/CLI_ZH.md)

---

## 前置条件

你需要至少安装一个编码 Agent：

<details>
<summary><b>OpenCode</b>（推荐）</summary>

```bash
curl -fsSL https://opencode.ai/install | bash
```

**必须配置：** 在 `~/.config/opencode/opencode.json` 中添加以下配置：

```json
{
  "permission": "allow"
}
```
</details>

<details>
<summary><b>Claude Code</b></summary>

```bash
npm install -g @anthropic-ai/claude-code
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
- **Socket Mode / WebSocket** — 没有公开 URL，没有 webhook
- **你的 token** — 存在 `~/.vibe_remote/`，永不上传
- **你的代码** — 留在你硬盘，只发给你选的 AI 提供商

---

## 卸载

```bash
vibe stop && uv tool uninstall vibe-remote && rm -rf ~/.vibe_remote
```

---

## 路线图

- [x] Slack 支持
- [x] Discord 支持
- [x] Telegram 支持
- [x] 微信支持
- [x] 飞书（Lark）支持
- [x] Web UI 设置向导 & 仪表盘
- [x] 按频道路由 Agent
- [x] 交互式提示（按钮、模态框）
- [x] 文件附件
- [ ] SaaS Mode
- [ ] Vibe Remote Coding Agent（一个 Agent 统领全局）
- [ ] Skills 管理器
- [ ] 最佳实践 & 多工作区指南

---

## 文档

- **[CLI 参考手册](docs/CLI_ZH.md)** — 命令行使用和服务生命周期
- **[Slack 配置指南](docs/SLACK_SETUP_ZH.md)** — 详细配置和截图
- **[Discord 配置指南](docs/DISCORD_SETUP_ZH.md)** — 详细配置和截图
- **[Telegram 配置指南](docs/TELEGRAM_SETUP_ZH.md)** — BotFather 初始化、令牌验证与会话发现
- **微信配置指南** — 运行 `vibe` 后在向导中选择微信即可
- **飞书配置指南** — 运行 `vibe` 后在向导中选择飞书即可

## 远程访问 Web UI

如果你希望从另一台设备打开本机 Web UI，或者把 Vibe Remote 部署在远端服务器上，请使用引导式远程访问配置：

```bash
vibe remote
```

它会引导你登录 avibe.bot、领取个人域名、完成配对，并启动安全 tunnel。

详见：**[CLI 参考手册](docs/CLI_ZH.md)**。

---

<div align="center">

**停止上下文切换。开始 vibe coding。**

[立即安装](#10-秒安装) · [配置指南](docs/SLACK_SETUP_ZH.md) · [报告 Bug](https://github.com/cyhhao/vibe-remote/issues) · [关注 @alex_metacraft](https://x.com/alex_metacraft)

---

*为随时随地写代码的开发者而建。*

</div>
