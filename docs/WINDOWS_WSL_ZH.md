# 在 Windows 上通过 WSL 使用 Vibe Remote

这篇文档面向希望在 Windows 电脑上使用 Vibe Remote 的用户。

如果你发现原生 Windows 运行某些 Agent CLI 不够稳定，或者希望获得更接近 Linux/macOS 的命令行体验，推荐使用 WSL 运行 Vibe Remote。

## 什么是 WSL

WSL 是 `Windows Subsystem for Linux`。

简单说，它让你可以在 Windows 里运行一个 Linux 用户态环境。对 Vibe Remote 这类依赖命令行工具、Python、Node、Agent CLI 的程序来说，WSL 通常比原生 Windows 更顺畅。

## 什么时候推荐用 WSL

推荐优先使用 WSL 的情况：

- 你主要使用 OpenCode
- 你希望尽量减少 Windows 原生兼容性问题
- 你习惯 Linux 命令行
- 你的项目本身已经在 WSL 开发

如果你主要使用 Claude Code 或 Codex，WSL 同样是一个很稳妥的方案。

## 总体思路

推荐的运行方式是：

- Windows 负责浏览器和聊天工具
- WSL 负责运行 Vibe Remote
- Agent CLI 也安装在 WSL 里
- 项目代码放在 WSL 的 Linux 文件系统里

这样做的好处是：

- 环境更统一
- 大多数命令行工具行为更接近官方文档
- 避免一部分原生 Windows 的进程和路径兼容问题

## 先决条件

开始前请确认：

- 你已经安装 WSL2
- 你已经安装一个 Linux 发行版，例如 Ubuntu
- 你能打开 WSL 终端
- 你的 Windows 浏览器可以访问本机地址

## 推荐目录

建议把项目放在 WSL 自己的 Linux 文件系统里，例如：

```bash
~/work
```

不建议优先把项目放在 `/mnt/c/...` 下长期使用。

原因是：

- 一些工具在 `/mnt/c/...` 下会更慢
- 文件监听、权限和路径行为更容易出现边角问题
- Agent CLI 在原生 Linux 路径里通常更稳定

## 安装 Vibe Remote

在 WSL 终端中执行：

```bash
curl -fsSL https://raw.githubusercontent.com/cyhhao/vibe-remote/master/install.sh | bash
```

安装完成后启动：

```bash
vibe
```

## 打开 Web UI

Vibe Remote 默认会在运行它的那台机器上监听：

```text
http://127.0.0.1:5123
```

在现代 WSL2 环境里，通常可以直接在 Windows 浏览器里打开：

```text
http://127.0.0.1:5123
```

如果浏览器没有自动弹出，手动打开上面的地址即可。

## 安装 Agent CLI

重要：请把你要使用的 Agent CLI 也安装在 WSL 里，而不是只安装在 Windows 里。

例如：

- `claude`
- `codex`
- `opencode`

Vibe Remote 会从它自己的运行环境里调用这些命令。如果 Vibe Remote 跑在 WSL，那么这些 CLI 也应该能在 WSL 终端里直接执行。

你可以先在 WSL 里确认：

```bash
which claude
which codex
which opencode
```

如果某个命令找不到，请先在 WSL 内完成安装。

## 设置工作目录

在 Web UI 的设置向导或 Dashboard 中，把默认工作目录设置成 WSL 路径，例如：

```text
/home/<your-user>/work
```

不要把默认工作目录优先设成 Windows 风格路径，例如：

```text
C:\Users\...
```

也不要优先设成：

```text
/mnt/c/Users/...
```

如果你必须访问 Windows 文件，也可以临时使用 `/mnt/c/...`，但不建议把它作为长期默认工作目录。

## 推荐的使用方式

### 方案 A：全部跑在 WSL

这是最推荐的方式。

- Vibe Remote 跑在 WSL
- Agent CLI 跑在 WSL
- 项目代码放在 WSL
- 浏览器在 Windows 中访问 `http://127.0.0.1:5123`

### 方案 B：Windows 只负责界面

这也是推荐做法。

- Slack / Discord / 微信 / 飞书客户端照常装在 Windows
- 浏览器在 Windows 中打开 Web UI
- 所有实际执行代码的命令都在 WSL 内完成

## 常见问题

### 1. 浏览器没有自动打开

这是正常的。

直接在 Windows 浏览器中打开：

```text
http://127.0.0.1:5123
```

### 2. 在 Web UI 里点安装 Agent 失败

如果你在 WSL 中运行 Vibe Remote，而在 Windows 浏览器中操作，有时本地安全校验可能导致安装接口失败。

如果出现这种情况，不要卡在 UI 按钮里，直接在 WSL 终端里手动安装对应 CLI，然后回到 Web UI 填路径或重新检测即可。

### 3. OpenCode 在 Windows 原生环境里不够稳定

如果你主要使用 OpenCode，优先推荐在 WSL 中运行它。

这通常比原生 Windows 更稳定，也更接近 OpenCode 官方推荐的使用方式。

### 4. 可以在 WSL 里用 Docker 吗

可以，但前提是你的 Docker Desktop 已开启 WSL 集成，或者你自己在 WSL 里具备可用的 Docker 环境。

如果你只是在本机日常使用 Vibe Remote，本身并不要求必须用 Docker。

## 快速检查清单

在 WSL 中确认以下命令都正常：

```bash
vibe version
vibe doctor
which claude
which codex
which opencode
```

然后在 Windows 浏览器中确认：

```text
http://127.0.0.1:5123
```

可以正常打开。

## 一句话建议

如果你在 Windows 上想获得更稳定的 Vibe Remote 使用体验，推荐采用下面这套组合：

- Windows：浏览器 + 聊天工具
- WSL：Vibe Remote + Agent CLI + 代码仓库

这通常是当前最稳妥、最省心的方案。
