## TL;DR

```bash
vibe
```

在向导中选择 **Discord**，粘贴机器人令牌，验证后选择服务器并启用频道。

---

## 第一步：创建 Discord 应用

1. 打开 [Discord Developer Portal](https://discord.com/developers/applications)
2. 点击 **New Application** 并命名
3. 进入 **Bot** → **Add Bot**
4. 复制 **Bot Token**

---

## 第二步：启用 Intents

在 **Bot → Privileged Gateway Intents** 中启用：

- Message Content Intent
- Server Members Intent（可选）

---

## 第三步：邀请机器人

1. 进入 **OAuth2 → URL Generator**
2. 勾选 **bot** scope
3. 添加权限：
   - Read Messages/View Channels
   - Send Messages
   - Create Public Threads
   - Send Messages in Threads
   - Add Reactions
   - Attach Files
4. 打开生成的 URL，把机器人加入服务器

---

## 第四步：向导配置

1. 运行 `vibe`
2. 选择 **Discord**
3. 粘贴 Bot Token → **Validate Token**
4. 选择服务器
5. 在 **Channels** 步骤启用频道

---

## 说明

- 为了对齐 Slack 的会话体验，每条新消息会创建一个 Discord 线程。
- 在私信中，Discord 不支持线程，但会话仍按消息隔离。
