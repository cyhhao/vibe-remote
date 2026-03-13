# Architecture Cleanup Plan

> **Branch**: `refactor/architecture-cleanup`
> **Prerequisite**: PR #69 (settings/auth refactoring) merged
> **Status**: Planning

## 1. Background

PR #69 解决了 settings store / auth pipeline 层面的 5 个根因问题。但在 review 过程中对整体架构做了一次全面审计，发现还有 4 类系统性技术债务。这些债务不影响功能正确性，但拖慢开发速度、增加 bug 引入风险、阻碍新平台接入。

**审计数据摘要**：

| 指标 | 当前值 |
|------|--------|
| controller.py | 1,270 行 / 35 方法 / 12 种职责 |
| IM 层总行数 | 7,565 行（slack 3,224 + discord 1,648 + feishu 2,693） |
| IM 层预估重复率 | ~60-65% |
| settings_manager.py | 690 行 / 40 方法 / 5 种不相关职责 |
| Handler 间重复方法 | `_get_lang()`, `_t()`, `_get_settings_key()` × 4 份 |

## 2. 问题详解

### 问题 A：Controller God Object

**现状**：`core/controller.py` 承载 12 种职责，其中 ~652 行是 handler 级别的业务逻辑（5 个 modal/submission handler），本应在 handler 层。

**5 个应下沉的方法**：

| 方法 | 行数 | 应归属 |
|------|------|--------|
| `handle_settings_update` | 76 | `SettingsHandler` |
| `handle_change_cwd_submission` | 27 | `CommandHandlers` |
| `handle_resume_session_submission` | 85 | `SessionHandler` |
| `handle_routing_modal_update` | 174 | `SettingsHandler`（或新建 `RoutingHandler`） |
| `handle_routing_update` | 112 | `SettingsHandler`（或新建 `RoutingHandler`） |

**其他问题**：
- `emit_agent_message` 178 行，包含完整的消息合并/拆分/截断状态机。可提取为 `ConsolidatedMessageDispatcher` 类。
- `_init_modules` 中 3 个 `isinstance` 检查 + 平台特定导入，破坏了 `IMFactory` 的抽象。
- `handle_routing_modal_update` 解析 Slack Block Kit 的 `block_id`/`action_id`，纯 Slack 特定逻辑不该在 core 层。

### 问题 B：IM 层巨量 Copy-Paste

**现状**：3 个 IM 客户端独立实现了相同的业务流程，只是 UI 序列化格式不同。`BaseIMClient`（353 行）只抽象了消息传输层，未覆盖 UI 构建和入站处理。

**重复最严重的 5 块**：

| 功能 | Slack | Discord | Feishu | 重复性质 |
|------|-------|---------|--------|---------|
| 消息处理主循环 | 238 行 | 127 行 | 175 行 | 业务流程相同，提取数据的 hook 不同 |
| Routing Modal | 523 行 | 441 行 | 315 行 | 选项构造逻辑完全相同，只是序列化格式不同 |
| Settings Modal | 223 行 | 216 行 | 142 行 | 选项构造逻辑相同 |
| Resume Session Modal | 163 行 | 105 行 | 149 行 | 选项构造逻辑相同 |
| Auth Denial | 27 行 | 22 行 | 11 行 | 三种拒绝分支 + i18n key 完全一致 |

**单个最长方法**：`SlackBot._build_routing_modal_view` = 523 行。

### 问题 C：SettingsManager / SettingsStore 分层不清

**三个子问题**：

**C1. 双重 dataclass**：
- `settings_manager.UserSettings` ≈ `v2_settings.ChannelSettings`
- `settings_manager.ChannelRouting` ≈ `v2_settings.RoutingSettings`
- 4 个纯搬运字段的转换方法：`_from_channel_settings`, `_to_channel_settings`, `_from_bound_user_settings`, `_sync_to_bound_user`

**C2. Leaky facade**：
- IM 层 15 处直接 `self.settings_manager.store` 穿透访问（主要是传给 `check_auth(store=...)`）
- `vibe/api.py` 完全绕过 SettingsManager 直接操作 SettingsStore

**C3. God object（690 行 / 40 方法）**：
- 真正的 settings 逻辑：~20 方法
- Session mapping / thread tracking / message dedup / active polls：~20 方法，全部委托给 `SessionsStore`
- 23% 的方法是 pass-through 或 alias

**C4. 业务逻辑下沉到存储层**：
- `SettingsStore.bind_user_with_code`：验证码 + 首用户自动 admin + 原子绑定，是完整业务流程
- `SettingsStore.set_admin`：enforces "不能移除最后一个 admin"

### 问题 D：Handler 缺少 Base Class

**现状**：4 个 handler 各自 copy-paste 初始化 + 3 个工具方法。

```python
# 重复 4 次的模式
class XxxHandler:
    def __init__(self, controller):
        self.controller = controller
        self.config = controller.config
        self.im_client = controller.im_client
        self.settings_manager = controller.settings_manager
        ...

    def _get_lang(self): ...      # 同一实现 × 4
    def _t(self, key, **kw): ...  # 同一实现 × 4
    def _get_settings_key(self, context): ...  # 同一实现 × 4
```

另外 `message_handler.handle_callback_query()` 每次调用都新建 `SettingsHandler` 和 `CommandHandlers` 实例，而不是复用 controller 上已有的实例。

## 3. 重构方案

### Phase A：Handler Base Class + Controller 瘦身

**目标**：消除 handler 间 copy-paste，将 controller 中的 handler 逻辑下沉。

**Phase A1：Handler Base Class**

```python
# core/handlers/base.py (新建)
class BaseHandler:
    """Shared base for all handlers."""

    def __init__(self, controller):
        self.controller = controller
        self.config = controller.config
        self.im_client = controller.im_client
        self.settings_manager = controller.settings_manager
        self.formatter = getattr(controller, "formatter", None)

    def _get_lang(self) -> str:
        return self.config.language or "en"

    def _t(self, key: str, **kwargs) -> str:
        from vibe.i18n import t
        return t(key, self._get_lang(), **kwargs)

    def _get_settings_key(self, context: MessageContext) -> str:
        is_dm = (context.platform_specific or {}).get("is_dm", False)
        return context.user_id if is_dm else context.channel_id
```

让 4 个 handler 继承 `BaseHandler`，删除各自的重复定义。

**Phase A2：下沉 5 个 handler 方法**

| 方法 | 从 controller 移至 | 改动要点 |
|------|-------------------|---------|
| `handle_settings_update` | `SettingsHandler` | 直接搬运，依赖通过 base 获取 |
| `handle_change_cwd_submission` | `CommandHandlers` | 直接搬运 |
| `handle_resume_session_submission` | `SessionHandler` | 需要传入 `claude_sessions` dict |
| `handle_routing_modal_update` | `SettingsHandler`（或新建 `RoutingHandler`） | **注意**：包含 Slack Block Kit 解析，后续 Phase B 进一步抽象 |
| `handle_routing_update` | `SettingsHandler`（或新建 `RoutingHandler`） | 直接搬运 |

Controller 的 `_setup_callbacks` 改为指向 handler 上的方法。

**Phase A3：修复 `handle_callback_query` 实例化**

```python
# Before: 每次新建
settings_handler = SettingsHandler(self.controller)
command_handler = CommandHandlers(self.controller)

# After: 复用已有实例
settings_handler = self.controller.settings_handler
command_handler = self.controller.command_handler
```

**Phase A4：提取 ConsolidatedMessageDispatcher（可选）**

`emit_agent_message` 的消息合并/拆分状态机（~178 行）可提取为独立类，但因为与 controller 的 `im_client` 和 `settings_manager` 耦合较深，改动量不小。建议在 A1-A3 完成后评估是否值得。

**预期收益**：
- Controller 从 1,270 行降至 ~600 行
- 消除 12 处 `_get_lang`/`_t`/`_get_settings_key` 重复
- Handler 职责清晰，新功能知道该加在哪里

### Phase B：IM 消息处理主循环抽象

**目标**：消除 3 个平台各自 ~200 行的消息处理主循环重复。

**设计思路**：在 `BaseIMClient` 中实现共享的入站消息处理流程，平台子类只实现数据提取 hook。

```python
# modules/im/base.py
class BaseIMClient:
    async def process_inbound_message(self, raw_event: dict) -> Optional[MessageContext]:
        """Shared inbound message processing pipeline.

        Subclasses implement the extract_* hooks.
        """
        # 1. Extract basic info
        event_data = self.extract_event_data(raw_event)
        if event_data is None:
            return None

        # 2. Dedup check
        if self._is_duplicate(event_data):
            return None

        # 3. Config hot-reload
        self._refresh_config()

        # 4. Build context with is_dm
        context = self.build_message_context(event_data)

        # 5. Auth check
        auth = check_auth(
            user_id=context.user_id,
            channel_id=context.channel_id,
            is_dm=context.platform_specific.get("is_dm", False),
            action=self._parse_command_action(event_data.text),
            store=self.settings_manager.store if self.settings_manager else None,
        )
        if not auth.allowed:
            await self._send_auth_denial(context.channel_id, context.user_id, auth)
            return None

        # 6. require_mention + thread check
        if not self._should_process(event_data, context):
            return None

        # 7. Parse commands
        if self._is_command(event_data.text):
            await self._dispatch_command(event_data, context)
            return None

        # 8. Dispatch to message handler
        return context

    # --- Platform hooks (abstract) ---
    def extract_event_data(self, raw_event) -> Optional[EventData]: ...
    def build_message_context(self, event_data: EventData) -> MessageContext: ...
    def _should_process(self, event_data, context) -> bool: ...
```

**注意**：这个改动风险最高，因为 3 个平台各自有微妙差异（Slack 有 `app_mention` 事件、Feishu 有 shared content 提取、Discord 有 guild/thread 检查）。需要仔细设计 hook 点，避免过度抽象。

**建议**：先只抽 auth + dedup + command 解析这几个高置信度的共享步骤，不要试图抽象整个 pipeline。

**预期收益**：
- 减少 ~300-400 行重复
- 新平台接入只需实现 hook，不需要复制整个主循环
- Auth 逻辑不会再漏

### Phase C：SettingsManager 拆分

**目标**：将 SettingsManager 的 5 种职责分离为清晰的模块。

**Phase C1：分离 SessionsFacade**

SettingsManager 的 ~20 个 session/thread/dedup/poll 方法全部委托给 `SessionsStore`。将它们提取为独立的 `SessionsFacade` 类（或直接让消费方使用 `SessionsStore`）。

```python
# Before: controller.settings_manager.set_agent_session_mapping(...)
# After:  controller.sessions.set_agent_session_mapping(...)
```

消费方：`session_handler.py`, `message_handler.py`, `controller.py`（emit_agent_message 中的 session 追踪）。

**Phase C2：统一 dataclass**

消除 `settings_manager.UserSettings` / `settings_manager.ChannelRouting`，直接使用 `v2_settings.ChannelSettings` / `v2_settings.RoutingSettings`。删除 4 个转换方法。

这要求 `v2_settings` 的 dataclass 足够好用（字段名、默认值等），可能需要小幅调整。

**Phase C3：封装 store 访问**

IM 层 15 处 `self.settings_manager.store` 穿透。主要用途是传给 `check_auth(store=...)`。解决方案：

```python
# Option A: SettingsManager 暴露 auth 需要的方法
class SettingsManager:
    def get_store(self) -> SettingsStore:
        """Explicit store access for auth pipeline."""
        return self.store

# Option B: check_auth 接受 SettingsManager 而非 SettingsStore
def check_auth(..., settings_manager=None):
    store = settings_manager.store if settings_manager else None
```

Option A 更诚实（store 就是需要被外部访问的），Option B 增加了不必要的间接层。**建议 Option A**。

**预期收益**：
- SettingsManager 从 690 行 / 40 方法降至 ~400 行 / ~20 方法
- 消除双重 dataclass 和 4 个转换方法
- 职责边界清晰：SettingsManager 管 settings，SessionsFacade 管 sessions

### Phase D：IM Modal 逻辑抽象（长期）

**目标**：消除 3 个平台 ~1000+ 行的 modal 构建重复。

**设计思路**：引入平台无关的 Modal Data Model + 平台特定 Renderer。

```python
# core/modals.py (新建)
@dataclass
class ModalField:
    id: str
    label: str
    type: Literal["select", "multi_select", "text_input"]
    options: List[ModalOption] = field(default_factory=list)
    default: Any = None

@dataclass
class ModalDefinition:
    title: str
    fields: List[ModalField]
    submit_label: str = "Save"

# 构建 modal 数据（平台无关）
def build_settings_modal(user_settings, message_types, ...) -> ModalDefinition:
    ...

def build_routing_modal(current_routing, backends, ...) -> ModalDefinition:
    ...
```

每个平台实现 `render_modal(definition: ModalDefinition) -> PlatformView`。

**风险**：
- 三个平台的 UI 能力差异大（Slack Block Kit vs Discord Views vs Feishu Cards）
- 过度抽象可能导致 "lowest common denominator" 问题
- 投入产出比不一定划算

**建议**：先做 Phase A-C，观察实际开发速度提升后再决定是否做 Phase D。如果后续不再新增 IM 平台，这个投入可能不值得。

## 4. 实现顺序与依赖

```
Phase A1 (Handler Base Class)
    ↓
Phase A2 (Controller 瘦身) ← depends on A1
    ↓
Phase A3 (fix callback_query)  ← independent, can merge early
    ↓
Phase B (IM 主循环抽象)  ← independent of A
    ↓
Phase C1 (SessionsFacade 分离) ← independent of A/B
    ↓
Phase C2 (统一 dataclass) ← after C1
    ↓
Phase C3 (封装 store 访问) ← after C2
    ↓
Phase D (Modal 抽象) ← after all above, optional
```

Phase A1-A3 和 Phase C1 可以并行。Phase B 独立但风险最高，建议放在 A 完成后。

## 5. 文件改动预估

| Phase | 新建文件 | 修改文件 | 预估行数变化 |
|-------|---------|---------|-------------|
| A1 | `core/handlers/base.py` | 4 handler files | +50 / -60 |
| A2 | — | controller.py, 3 handler files, 3 IM files | +500 / -650 |
| A3 | — | message_handler.py | +2 / -4 |
| B | — | base.py, slack.py, discord.py, feishu.py | +150 / -400 |
| C1 | `modules/sessions_facade.py`（可选） | controller.py, session_handler.py, message_handler.py, settings_manager.py | +100 / -80 |
| C2 | — | settings_manager.py, v2_settings.py | +20 / -120 |
| C3 | — | settings_manager.py | +5 / -0 |
| D | `core/modals.py` | 3 IM files, settings_handler.py | +300 / -800 |

## 6. 风险与缓解

| 风险 | 缓解 |
|------|------|
| Phase A2 搬运时遗漏 controller 内部状态引用 | 每步 lint + 手动验证一个平台 |
| Phase B 过度抽象导致平台特性丢失 | 只抽高置信度步骤（auth/dedup/command），保留平台 hook |
| Phase C2 统一 dataclass 破坏序列化 | 先写转换测试，确认 JSON 格式不变 |
| 改动量大导致引入回归 | 每 phase 独立 PR，可增量 merge |

## 7. 验证清单

- [ ] 每 phase lint 通过
- [ ] Bot 启动正常（至少 Slack 平台）
- [ ] 消息收发正常
- [ ] Settings / Routing / CWD modal 正常
- [ ] DM bind 流程正常
- [ ] Admin 权限控制正常
