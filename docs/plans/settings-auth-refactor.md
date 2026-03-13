# Settings Store & Auth Pipeline Refactoring Plan

> **Issue**: #71
> **Branch**: `feature/admin-permission`
> **Status**: Planning → Implementation

## 1. Background

PR #69 添加了 DM bind 系统和 admin 权限控制，经历了 5+ 轮 review，每轮都在不同的代码路径中发现新的鉴权绕过或数据不一致 bug。根因不在于个别 bug，而是以下 5 个架构问题互相叠加放大。

## 2. 问题详解

### 问题 1：双 SettingsStore 实例，无缓存同步

**现状**：

- `SettingsManager.store`（Bot 进程）：启动时创建一次，常驻内存
- `vibe/api.py`（UI API）：每个请求 handler 都 `SettingsStore()` 新建实例（共 11 处）

```python
# api.py — 每次请求新建实例
def create_bind_code(...):
    store = SettingsStore()  # 从磁盘读
    store.create_bind_code()
    store.save()  # 写回磁盘
    # store 被丢弃

# settings_manager.py — 长期实例
class SettingsManager:
    def __init__(self):
        self.store = SettingsStore(self.settings_file)  # 启动时创建，此后不重新读磁盘
```

**影响**：UI 创建 bind code → Bot 验证时找不到 → 用户绑定失败。当前通过在 `bind_user_with_code()` 内加 `self._load()` 打补丁，但其他读取路径（`is_bound_user`、`is_admin`）仍可能读到过期数据。

### 问题 2：8 入口 × 内联鉴权，缺乏统一中间件

当前鉴权逻辑分散在 8 个入口各自实现：

| 平台    | 入口                        | 文件:行号                    | 鉴权内容                 |
|---------|-----------------------------|-----------------------------|-------------------------|
| Slack   | 消息 handler                | `slack.py:898-918`          | bind gate + channel auth |
| Slack   | 斜杠命令 handler            | `slack.py:1044-1067`        | bind gate + channel auth |
| Slack   | 交互组件 handler            | `slack.py:1164-1171`        | channel auth only        |
| Discord | 消息 handler                | `discord.py:457-475`        | bind gate + channel auth |
| Discord | PersistentStartView.on_click| `discord.py:~1500`          | channel auth only        |
| Discord | DiscordButtonView.on_click  | `discord.py:~1582`          | channel auth only        |
| Feishu  | 消息 handler                | `feishu.py:1100-1120`       | bind gate + channel auth |
| Feishu  | 卡片 action handler         | `feishu.py:1220-1265`       | bind/channel + admin     |

**影响**：每次加新鉴权规则（如 admin 检查），需要在 8 个地方都加，漏掉一个就是安全绕过。

### 问题 3：DM 检测方式碎片化

| 平台    | 消息上下文           | 交互上下文                  | 可靠性   |
|---------|---------------------|-----------------------------|----------|
| Slack   | `channel_id.startswith("D")` | 同左                   | ✅ 高    |
| Discord | `isinstance(ch, DMChannel)` | `guild is None`         | ✅ 高    |
| Feishu  | `chat_type == "p2p"`        | **"不在已知频道=DM"推测** | ❌ 脆弱  |

Feishu 卡片 action 的回调数据没有 `chat_type`，当前用反向推理（`chat_id not in channels → is_dm`）。新频道会被误判为 DM。

### 问题 4：settings_key 双轨解析

两个函数做同一件事但逻辑不同：

- `_get_settings_key(context: MessageContext)` — 用平台原生对象判断，可靠
- `_resolve_settings_key(user_id, channel_id)` — 仅字符串，用启发式推理

`_resolve_settings_key` 仅在 2 处使用（`handle_settings_update:800`, `handle_routing_update:1169`），应该消除。

### 问题 5：channel/user 设置混用同一个 dict

`SettingsManager.settings` 用一个 dict 同时存频道和用户的运行时设置。保存时按 `_is_bound_user_key()` 分流到 `store.settings.channels` 或 `store.settings.users`。

**影响**：用户被解绑后，其 key 在 runtime dict 中残留，被误写入 `channels` → 产生幽灵频道记录。

## 3. 重构方案

### Phase 1：单例 SettingsStore + 自动重载

**目标**：消除双实例问题，所有代码共享一个 store。

**设计**：

```python
# config/v2_settings.py
class SettingsStore:
    _instance: Optional["SettingsStore"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def get_instance(cls, settings_path: Optional[Path] = None) -> "SettingsStore":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(settings_path)
            return cls._instance

    @classmethod
    def reset_instance(cls):
        """For testing only."""
        with cls._instance_lock:
            cls._instance = None

    def __init__(self, settings_path=None):
        self.settings_path = settings_path or paths.get_settings_path()
        self._file_mtime: float = 0
        self._bind_lock = threading.Lock()
        self.settings = SettingsState()
        self._load()

    def _maybe_reload(self):
        """If file changed on disk, reload. Called before reads."""
        try:
            mtime = self.settings_path.stat().st_mtime
            if mtime > self._file_mtime:
                self._load()
        except FileNotFoundError:
            pass

    def _load(self):
        # ... existing logic ...
        try:
            self._file_mtime = self.settings_path.stat().st_mtime
        except FileNotFoundError:
            self._file_mtime = 0
```

**改动文件**：

| 文件 | 改动 |
|------|------|
| `config/v2_settings.py` | 添加 `get_instance()`、`reset_instance()`、`_maybe_reload()`、`_file_mtime` |
| `vibe/api.py` | 所有 `SettingsStore()` → `SettingsStore.get_instance()`，读操作前加 `store._maybe_reload()` |
| `modules/settings_manager.py` | `self.store = SettingsStore.get_instance(self.settings_file)` |
| `config/v2_settings.py` | `bind_user_with_code` 中移除手动 `self._load()`（被 `_maybe_reload` 取代） |

**注意**：`SettingsManager._reload_if_changed()` 中原有的 mtime+fingerprint 双重检查逻辑可以简化，因为 `SettingsStore._maybe_reload()` 已经处理了文件变更检测。但 `SettingsManager` 仍需在 reload 时重建自己的 runtime dict（`self.settings`），所以 `_reload_if_changed` 不能完全移除，需要改为监听 store 的重载事件或在 `get_user_settings` 时检查 store 是否已更新。

**简化方案**：让 `SettingsManager._reload_if_changed()` 改为检查 store 的 `_file_mtime`：

```python
def _reload_if_changed(self):
    old_mtime = self.store._file_mtime
    self.store._maybe_reload()
    if self.store._file_mtime != old_mtime:
        # Store reloaded from disk, rebuild runtime dict
        self._rebuild_runtime_settings()
```

### Phase 2：统一 DM 检测

**目标**：所有 MessageContext 在创建时就带有可靠的 `is_dm` 标记。

**设计**：每个平台在创建 MessageContext 时，在 `platform_specific` 中设置 `"is_dm": bool`。

**当前状态**：
- Discord：✅ 所有 3 个入口已设置 `"is_dm"`
- Feishu 消息：✅ 已设置 `"is_dm": is_p2p`
- Feishu 卡片 action：❌ 用反向推理
- Slack 消息：❌ 未设置 `is_dm`
- Slack 斜杠命令：❌ 未设置
- Slack 交互：❌ 未设置

**改动**：

#### Slack — 在 3 个入口添加 `is_dm`

```python
# 消息 handler (slack.py:935)
context = MessageContext(
    ...,
    platform_specific={
        ...,
        "is_dm": channel_id.startswith("D"),
    },
)

# 斜杠命令 handler (slack.py:1077)
context = MessageContext(
    ...,
    platform_specific={
        ...,
        "is_dm": is_dm,  # 已有 is_dm 变量
    },
)

# 交互 handler (slack.py:1186)
context = MessageContext(
    ...,
    platform_specific={
        ...,
        "is_dm": is_dm,  # 已有 is_dm 变量
    },
)
```

#### Feishu 卡片 action — DM chat_id 注册表

当前的反向推理不可靠。改用正向记录：

```python
class FeishuClient:
    def __init__(self, ...):
        ...
        self._dm_chat_ids: Set[str] = set()

    async def _async_handle_message(self, ...):
        ...
        if is_p2p:
            self._dm_chat_ids.add(chat_id)  # 记录 DM chat_id
        ...

    async def _async_handle_card_action(self, ...):
        ...
        is_dm = chat_id in self._dm_chat_ids
        context = MessageContext(
            ...,
            platform_specific={
                ...,
                "is_dm": is_dm,
            },
        )
```

**局限性**：Bot 重启后 `_dm_chat_ids` 为空，直到 DM 用户发一条消息才会被重新填充。但这是可接受的，因为卡片 action 前通常已有消息交互。如果需要更强保证，可以在启动时从 `store.settings.users` 的 bound user 列表推断。

**启动时预填充**（可选增强）：

```python
async def _on_ready(self):
    # Pre-populate DM chat IDs from bound users
    # This handles the case where bot restarts and users click old DM cards
    if self.settings_manager:
        for user_id in self.settings_manager.store.settings.users:
            # We don't have the chat_id for each user stored,
            # so we can't pre-populate. Fall back to conservative behavior:
            # if user is bound and chat_id is unknown, treat as DM.
            pass
```

实际上更好的方案是：在 `UserSettings`（`v2_settings.py`）中增加一个 `dm_chat_id` 字段，在 bind 时记录。这样重启后也能可靠判断。

```python
@dataclass
class UserSettings:
    display_name: str = ""
    is_admin: bool = False
    bound_at: str = ""
    enabled: bool = True
    dm_chat_id: str = ""  # 新增：记录该用户的 DM chat_id
    ...
```

在 bind 成功时记录 `dm_chat_id = context.channel_id`，启动时从 store 预填 `_dm_chat_ids`。

### Phase 3：统一鉴权管道

**目标**：所有入口的鉴权检查收敛到一处。

**设计**：

```python
# core/auth.py (新建)
from dataclasses import dataclass
from typing import Optional

# 需要 admin 权限的操作
ADMIN_PROTECTED_ACTIONS = frozenset({
    # Button callbacks
    "cmd_settings", "cmd_routing", "cmd_change_cwd", "vibe_update_now",
    # Feishu form submissions
    "cwd_submit", "settings_submit", "routing_backend_select", "routing_submit",
    # Text commands
    "set_cwd", "settings",
})

# 从 bind gate 豁免的命令（未绑定用户也能用）
BIND_EXEMPT_COMMANDS = frozenset({"bind"})


@dataclass
class AuthResult:
    allowed: bool
    denial: str = ""   # "unbound_dm" | "unauthorized_channel" | "not_admin" | ""
    is_dm: bool = False


def check_auth(
    *,
    user_id: str,
    channel_id: str,
    is_dm: bool,
    action: str = "",
    store=None,  # SettingsStore instance
) -> AuthResult:
    """Centralized authorization check.

    Parameters:
        user_id: The user performing the action
        channel_id: The channel where the action occurs
        is_dm: Whether this is a DM context
        action: The action being performed (command name, callback_data, form button_name)
        store: SettingsStore instance for looking up permissions
    """
    if store is None:
        return AuthResult(allowed=True, is_dm=is_dm)

    # 1. DM bind gate
    if is_dm:
        if action in BIND_EXEMPT_COMMANDS:
            return AuthResult(allowed=True, is_dm=True)
        if not store.is_bound_user(user_id):
            return AuthResult(allowed=False, denial="unbound_dm", is_dm=True)
    else:
        # 2. Channel authorization
        ch = store.settings.channels.get(channel_id)
        if not ch or not ch.enabled:
            return AuthResult(allowed=False, denial="unauthorized_channel", is_dm=False)

    # 3. Admin check for protected actions
    if action in ADMIN_PROTECTED_ACTIONS:
        if store.has_any_admin() and not store.is_admin(user_id):
            return AuthResult(allowed=False, denial="not_admin", is_dm=is_dm)

    return AuthResult(allowed=True, is_dm=is_dm)
```

**改动要点**：

每个 IM 入口点改为：

```python
# Before (8个地方各自实现)
if channel_id.startswith("D") and self.settings_manager:
    store = self.settings_manager.store
    if not store.is_bound_user(user_id):
        ...
        return
if not channel_id.startswith("D") and not await self._is_authorized_channel(channel_id):
    ...
    return

# After (统一调用)
from core.auth import check_auth

is_dm = channel_id.startswith("D")  # 或从 context 获取
auth = check_auth(
    user_id=user_id,
    channel_id=channel_id,
    is_dm=is_dm,
    action=command_name,  # 或 callback_data
    store=self.settings_manager.store if self.settings_manager else None,
)
if not auth.allowed:
    await self._send_auth_denial(channel_id, user_id, auth)
    return
```

每个平台实现一个 `_send_auth_denial(channel_id, user_id, auth_result)` 处理拒绝响应（平台特定的消息格式/发送方式）。

**同时移除以下冗余逻辑**：

| 位置 | 移除内容 |
|------|---------|
| `core/controller.py:279-298` | `_admin_guard` wrapper |
| `core/handlers/message_handler.py:304-315` | `handle_callback_query` 中的 admin 检查 |
| `modules/im/feishu.py:1259-1264` | 卡片 action 中的 admin form 检查 |
| 各 IM 客户端 | 所有内联 bind gate + channel auth 代码 |

`handle_callback_query` 中的 admin 检查可以移除，因为所有 callback 在进入 controller 之前已经过 auth pipeline。但为了 defense-in-depth，也可以保留一层薄检查。**建议移除**，避免双重维护。

### Phase 4：统一 settings_key

**目标**：消除 `_resolve_settings_key`，所有路径用同一个方法。

**设计**：

```python
# core/controller.py
def _get_settings_key(self, context: MessageContext) -> str:
    """DM → user_id, channel → channel_id."""
    is_dm = (context.platform_specific or {}).get("is_dm", False)
    return context.user_id if is_dm else context.channel_id
```

所有平台特定检测（`_is_discord_dm`、`_is_lark_dm`、`startswith("D")`）全部移除。因为 Phase 2 已保证 `is_dm` 在 context 创建时就正确设置。

**`_resolve_settings_key` 的消除**：

这个方法目前在 2 处使用：
- `handle_settings_update(self, user_id, ..., channel_id, ...)` (controller.py:800)
- `handle_routing_update(self, user_id, ..., channel_id, ...)` (controller.py:1169)

这两个是从 IM 层的 modal/form submission callback 调用的。它们没有 `MessageContext`，只有 `user_id` 和 `channel_id`。

**方案**：让这些 callback 也传递 `is_dm` 参数。

```python
# Before
on_settings_update=self.handle_settings_update  # (user_id, ..., channel_id, ...)

# After
on_settings_update=self.handle_settings_update  # (user_id, ..., channel_id, ..., is_dm=False)
```

IM 层在调用 callback 时已知 `is_dm`（来自 context），传递即可。

或者更简单：让这些 handler 也接收 `MessageContext`，统一接口。但这改动较大，先用增加 `is_dm` 参数的方案。

### Phase 5：分离 channel/user 运行时设置

**目标**：消除 `SettingsManager.settings` dict 中 channel/user 混用。

**设计**：

```python
class SettingsManager:
    def __init__(self, ...):
        self.channel_settings: Dict[str, UserSettings] = {}
        self.dm_user_settings: Dict[str, UserSettings] = {}

    def get_settings(self, key: str, is_dm: bool = False) -> UserSettings:
        """Get runtime settings by key."""
        target = self.dm_user_settings if is_dm else self.channel_settings
        ...

    def update_settings(self, key: str, settings: UserSettings, is_dm: bool = False):
        target = self.dm_user_settings if is_dm else self.channel_settings
        target[key] = settings
        self._save_settings()

    def _save_settings(self):
        # Channel settings → store.settings.channels
        channels = {}
        for cid, s in self.channel_settings.items():
            channels[cid] = self._to_channel_settings(s)
        self.store.settings.channels = channels

        # DM user settings → store.settings.users (sync runtime fields)
        for uid, s in self.dm_user_settings.items():
            self._sync_to_bound_user(uid, s)

        self.store.save()

    def _rebuild_runtime_settings(self):
        """Rebuild runtime dicts from store (called after store reload)."""
        self.channel_settings = {}
        self.dm_user_settings = {}
        for cid, cs in self.store.settings.channels.items():
            self.channel_settings[cid] = self._from_channel_settings(cs)
        for uid, us in self.store.settings.users.items():
            self.dm_user_settings[uid] = self._from_bound_user_settings(us)
```

**移除**：
- `_is_bound_user_key()` — 不再需要
- `_load_settings()` 中的 "shadowed by users" 逻辑 — 不再需要
- 单一 `self.settings` dict — 被两个 dict 替代

**调用方改动**：

所有调用 `get_user_settings(settings_key)` 的地方需要传入 `is_dm`：

```python
# Before
settings_key = self._get_settings_key(context)
settings = self.settings_manager.get_user_settings(settings_key)

# After
is_dm = (context.platform_specific or {}).get("is_dm", False)
settings_key = context.user_id if is_dm else context.channel_id
settings = self.settings_manager.get_settings(settings_key, is_dm=is_dm)
```

这涉及较多调用点（`_get_settings_key` 被调用约 20+ 次），但改动模式统一。

为减少改动量，可以让 `_get_settings_key` 返回 `(key, is_dm)` 元组：

```python
def _get_settings_key(self, context: MessageContext) -> tuple[str, bool]:
    is_dm = (context.platform_specific or {}).get("is_dm", False)
    key = context.user_id if is_dm else context.channel_id
    return key, is_dm
```

但这破坏了所有 20+ 个调用点的接口。更好的方案是保持 `_get_settings_key` 返回 `str`，然后在 `SettingsManager` 内部判断：

```python
class SettingsManager:
    def get_settings(self, key: str, is_dm: bool = False) -> UserSettings:
        ...

    # 兼容旧调用的便捷方法
    def get_user_settings(self, user_id) -> UserSettings:
        """Legacy: auto-detect channel vs user."""
        normalized = str(user_id)
        if normalized in self.dm_user_settings:
            return self.dm_user_settings[normalized]
        if normalized in self.channel_settings:
            return self.channel_settings[normalized]
        # New key — need is_dm to decide. Fall back to checking store.
        if normalized in self.store.settings.users:
            return self.get_settings(normalized, is_dm=True)
        return self.get_settings(normalized, is_dm=False)
```

**最终决定**：保持 `_get_settings_key` 返回 str，在 `SettingsManager` 中用 `get_settings(key, is_dm)` 新方法，同时保留 `get_user_settings` 做兼容。逐步将调用方迁移到 `get_settings`。

## 4. 实现顺序与依赖

```
Phase 1 (Singleton Store)
    ↓
Phase 2 (DM Detection)  ← Phase 3 depends on is_dm flag
    ↓
Phase 3 (Auth Pipeline)  ← Phase 4 depends on unified auth
    ↓
Phase 4 (settings_key)   ← Phase 5 depends on clean key resolution
    ↓
Phase 5 (Key Space Split)
```

每个 Phase 完成后 commit 一次，确保可增量验证。

## 5. 文件改动清单

| 文件 | Phase | 改动概述 |
|------|-------|---------|
| `config/v2_settings.py` | 1, 2 | 单例、`_maybe_reload`、UserSettings 加 `dm_chat_id` |
| `vibe/api.py` | 1 | `SettingsStore()` → `SettingsStore.get_instance()` |
| `core/auth.py` | 3 | **新建**：`check_auth()`、`AuthResult`、`ADMIN_PROTECTED_ACTIONS` |
| `core/controller.py` | 3, 4 | 移除 `_admin_guard`，简化 `_get_settings_key`，删除 `_resolve_settings_key`、`_is_discord_dm`、`_is_lark_dm` |
| `core/handlers/message_handler.py` | 3 | 移除 `handle_callback_query` 中的内联 admin 检查 |
| `core/handlers/command_handlers.py` | 3 | 移除 `handle_bind` 中的内联 DM 检查（由 auth pipeline 处理） |
| `modules/im/slack.py` | 2, 3 | 所有 context 加 `is_dm`，内联鉴权替换为 `check_auth` |
| `modules/im/discord.py` | 3 | 内联鉴权替换为 `check_auth`（`is_dm` 已有） |
| `modules/im/feishu.py` | 2, 3 | `_dm_chat_ids` 注册表，内联鉴权替换为 `check_auth` |
| `modules/settings_manager.py` | 1, 5 | 用单例 store，分离 `channel_settings`/`dm_user_settings` |

## 6. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 重构范围大，可能引入回归 | 每 phase 独立 commit + lint check |
| `_is_authorized_channel` 可能有 async 逻辑（API 调用） | 保留 IM 层的 `_is_authorized_channel` 方法作为 `check_auth` 的补充（用于首次发现新频道时的 API 检查），auth pipeline 仅做基于 settings 的快速判断 |
| SettingsManager 调用方改动量大（20+ 处） | Phase 5 保留 `get_user_settings` 兼容方法，逐步迁移 |
| Feishu `_dm_chat_ids` 重启后为空 | 在 UserSettings 中记录 `dm_chat_id`，启动时预填 |

## 7. 验证清单

- [ ] `ruff check .` 通过
- [ ] Bot 启动正常（Slack/Discord/Feishu 各一次）
- [ ] UI 创建 bind code → Bot 端 `/bind` 立即可用（验证 Phase 1）
- [ ] 未绑定用户 DM 被拒（验证 Phase 3 bind gate）
- [ ] 非授权频道消息被拒（验证 Phase 3 channel auth）
- [ ] 非 admin 用户点 Settings/Routing/CWD 被拒（验证 Phase 3 admin check）
- [ ] DM 用户设置修改正确保存和读取（验证 Phase 4+5）
- [ ] 频道设置修改正确保存和读取（验证 Phase 5）
- [ ] Feishu 卡片 action 在 DM 中正确识别为 DM（验证 Phase 2）
