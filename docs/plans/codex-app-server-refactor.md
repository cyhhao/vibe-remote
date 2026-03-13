# Codex App-Server Refactor Plan

## Background

The current Codex agent (`modules/agents/codex_agent.py`) launches a **new subprocess per message** via `codex exec --json`. When a new message arrives while one is in-flight, the old process group is SIGKILLed and a fresh process is started. This means:

- **No true message insertion**: cannot send a follow-up message into an active session
- **No graceful interruption**: SIGKILL loses all context
- **Session resume is fragile**: `codex exec resume <id>` starts a new process each time

Codex CLI v0.77+ provides an `app-server` mode — a persistent subprocess communicating via **JSON-RPC 2.0 over stdio**. This is the same pattern used by the Claude agent (persistent subprocess + stdin/stdout) but with a standardized JSON-RPC protocol.

## Goal

Replace the one-shot `codex exec` subprocess model with a persistent `codex app-server` process, enabling:

1. **True message insertion** — send `turn/start` into an active thread without killing the process
2. **Graceful interruption** — `turn/interrupt` instead of SIGKILL
3. **Session context preservation** — one persistent process per working directory, threads managed by Codex internally
4. **Streaming events** — real-time `item/agentMessage/delta`, `item/commandExecution/outputDelta` notifications

## Protocol Summary (JSON-RPC 2.0 over stdio)

### Lifecycle
1. Launch: `codex app-server` (persistent, reads JSON-RPC from stdin, writes to stdout)
2. Client sends `initialize` request with `clientInfo`
3. Server responds with capabilities
4. Client sends `initialized` notification
5. Client sends `thread/start` to create a new thread (returns `threadId`)
6. Client sends `turn/start` with `threadId` + `input` to begin a turn
7. Server streams notifications: `turn/started`, `item/started`, `item/agentMessage/delta`, `item/completed`, `turn/completed`
8. Server may send `item/commandExecution/requestApproval` or `item/fileChange/requestApproval` — client must respond with approval
9. For follow-up messages: send another `turn/start` on the same `threadId`
10. For interruption: send `turn/interrupt` with `threadId` + `turnId`
11. For session resume across process restarts: `thread/resume` with `threadId`

### Key Types

**UserInput** (for `turn/start` input array):
- `{"type": "text", "text": "..."}` — text message
- `{"type": "image", "url": "..."}` — image URL
- `{"type": "localImage", "path": "..."}` — local image file

**ThreadStartParams**: `{approvalPolicy?, cwd?, model?, sandbox?, baseInstructions?, config?}`
**TurnStartParams**: `{threadId, input[], model?, effort?, approvalPolicy?, cwd?, sandboxPolicy?}`
**TurnInterruptParams**: `{threadId, turnId}`

**Server Notifications** (key ones):
- `thread/started` → `{threadId}`
- `turn/started` → `{threadId, turnId}`
- `turn/completed` → `{threadId, turnId, ...}`
- `item/started` → `{threadId, turnId, itemId, itemType}`
- `item/completed` → `{threadId, turnId, item: {type, ...}}`
- `item/agentMessage/delta` → `{threadId, turnId, itemId, delta}` (streaming text)
- `item/commandExecution/outputDelta` → `{threadId, turnId, itemId, delta}`
- `item/reasoning/summaryTextDelta` → `{threadId, turnId, itemId, delta}`
- `error` → `{threadId, turnId, error, willRetry}`

**Server Requests** (need client response):
- `item/commandExecution/requestApproval` → `{threadId, turnId, itemId}` — auto-approve
- `item/fileChange/requestApproval` → `{threadId, turnId, itemId}` — auto-approve

## Architecture

### New Files

```
modules/agents/codex/
    __init__.py          # re-exports CodexAgent
    transport.py         # CodexTransport: process lifecycle + JSON-RPC I/O
    session.py           # Thread/turn state management per Slack thread
    event_handler.py     # Maps server notifications → Slack messages
    agent.py             # CodexAgent: orchestrates transport + session + events
```

### Existing File Changes
- `modules/agents/codex_agent.py` — delete (replaced by `codex/agent.py`)
- `modules/agents/registry.py` — update import path
- `config/v2_compat.py` — no changes needed (binary path still used)

### Class Design

#### CodexTransport (`transport.py`)

Manages the persistent `codex app-server` subprocess.

```python
class CodexTransport:
    """Manages a persistent codex app-server subprocess with JSON-RPC 2.0 communication."""

    def __init__(self, binary: str, cwd: str, extra_args: list[str]):
        self._binary = binary
        self._cwd = cwd
        self._extra_args = extra_args
        self._process: Optional[Process] = None
        self._request_id: int = 0
        self._pending: dict[int, asyncio.Future] = {}   # id → Future for RPC responses
        self._write_lock = asyncio.Lock()
        self._initialized = False

    async def start(self) -> None:
        """Launch app-server, perform initialize handshake."""

    async def stop(self) -> None:
        """Graceful shutdown: close stdin, wait, then SIGTERM/SIGKILL."""

    async def send_request(self, method: str, params: dict) -> dict:
        """Send JSON-RPC request and await response."""

    async def send_notification(self, method: str, params: dict | None = None) -> None:
        """Send JSON-RPC notification (no response expected)."""

    async def _reader_loop(self) -> None:
        """Read stdout line-by-line, dispatch responses and notifications."""

    def on_notification(self, callback: Callable[[str, dict], Awaitable[None]]) -> None:
        """Register notification handler."""

    def on_server_request(self, callback: Callable[[int, str, dict], Awaitable[dict]]) -> None:
        """Register server request handler (for approval requests)."""

    @property
    def is_alive(self) -> bool: ...
```

#### CodexSessionManager (`session.py`)

Maps Slack thread → Codex thread/turn IDs.

```python
class CodexSessionManager:
    """Maps vibe-remote session keys to Codex thread/turn state."""

    def __init__(self):
        self._threads: dict[str, str] = {}          # base_session_id → threadId
        self._active_turns: dict[str, str] = {}     # base_session_id → turnId
        self._transport_map: dict[str, str] = {}     # base_session_id → transport_key

    def get_thread_id(self, base_session_id: str) -> Optional[str]: ...
    def set_thread_id(self, base_session_id: str, thread_id: str) -> None: ...
    def get_active_turn(self, base_session_id: str) -> Optional[str]: ...
    def set_active_turn(self, base_session_id: str, turn_id: str) -> None: ...
    def clear_active_turn(self, base_session_id: str) -> None: ...
    def clear(self, base_session_id: str) -> None: ...
```

#### CodexEventHandler (`event_handler.py`)

Translates server notifications into `emit_agent_message` calls.

```python
class CodexEventHandler:
    """Handles codex app-server notifications and maps them to Slack messages."""

    def __init__(self, agent: 'CodexAgent'):
        self._agent = agent
        self._pending_text: dict[str, str] = {}      # turnId → accumulated text
        self._message_buffers: dict[str, str] = {}    # itemId → text delta buffer

    async def handle_notification(self, method: str, params: dict, request: AgentRequest) -> None:
        """Dispatch a server notification to the appropriate handler."""

    async def _on_turn_started(self, params: dict, request: AgentRequest) -> None: ...
    async def _on_turn_completed(self, params: dict, request: AgentRequest) -> None: ...
    async def _on_item_completed(self, params: dict, request: AgentRequest) -> None: ...
    async def _on_agent_message_delta(self, params: dict, request: AgentRequest) -> None: ...
    async def _on_command_output_delta(self, params: dict, request: AgentRequest) -> None: ...
    async def _on_reasoning_delta(self, params: dict, request: AgentRequest) -> None: ...
    async def _on_error(self, params: dict, request: AgentRequest) -> None: ...
```

#### CodexAgent (`agent.py`)

Replaces `codex_agent.py`, orchestrates the above components.

```python
class CodexAgent(BaseAgent):
    name = "codex"

    def __init__(self, controller, codex_config):
        super().__init__(controller)
        self._config = codex_config
        self._transports: dict[str, CodexTransport] = {}     # cwd → transport
        self._session_mgr = CodexSessionManager()
        self._event_handlers: dict[str, CodexEventHandler] = {}
        self._active_requests: dict[str, AgentRequest] = {}  # base_session_id → request

    async def handle_message(self, request: AgentRequest) -> None:
        """
        1. Get or create transport for request.working_path
        2. Get or create thread (thread/start or thread/resume)
        3. If turn is active: turn/interrupt first, then turn/start
        4. Send turn/start with user's message
        5. Notifications flow through event_handler → Slack
        """

    async def handle_stop(self, request: AgentRequest) -> bool:
        """Send turn/interrupt for the active turn."""

    async def clear_sessions(self, settings_key: str) -> int:
        """Archive threads, stop transports scoped to settings_key."""

    async def _get_or_create_transport(self, cwd: str) -> CodexTransport:
        """Lazy-start a transport for the given working directory."""

    async def _handle_approval(self, request_id: int, method: str, params: dict) -> dict:
        """Auto-approve all command/file approval requests."""
```

## Key Design Decisions

### 1. One Transport Per Working Directory
Since `codex app-server` is scoped to a `cwd`, we maintain one persistent process per unique working directory. Multiple Slack threads in the same channel share the transport but have separate Codex threads.

### 2. Auto-Approve All Requests
Like the current `--dangerously-bypass-approvals-and-sandbox` flag, we auto-approve all `item/commandExecution/requestApproval` and `item/fileChange/requestApproval` server requests by responding with `{"approved": true}`.

### 3. Message Insertion via turn/interrupt + turn/start
When a new message arrives while a turn is active:
1. Send `turn/interrupt` for the active turn
2. Wait for `turn/completed` (with interrupted status)
3. Send new `turn/start` with the new message

This is graceful — Codex preserves the conversation context, unlike SIGKILL.

### 4. Thread Resume on Process Restart
If the transport crashes or is restarted, we use `thread/resume` with the stored `threadId` to restore conversation state.

### 5. Event-Driven Slack Messages
Map notifications directly:
- `item/completed` (type=agent_message) → `emit_agent_message("assistant", ...)`
- `item/completed` (type=command_execution) → `emit_agent_message("toolcall", ...)`
- `item/completed` (type=reasoning) → `emit_agent_message("assistant", "🧠 ...")`
- `turn/completed` → `emit_result_message(...)` (final result)
- `error` → `emit_agent_message("notify", "❌ ...")`
- `thread/started` → `emit_agent_message("system", ...)` (init message)

### 6. Pending Message Pattern (same as current)
Keep the "pending assistant message" pattern: buffer the last `agent_message` item, emit previous ones immediately, emit the last one as the result message on `turn/completed`.

## Implementation Order

### Phase 1: Transport Layer
- [ ] Create `modules/agents/codex/__init__.py`
- [ ] Implement `CodexTransport` in `transport.py`
  - Process lifecycle (start, stop, crash detection)
  - JSON-RPC request/response correlation
  - Notification dispatch
  - Server request handling (approval auto-approve)
  - Stderr monitoring

### Phase 2: Session Management
- [ ] Implement `CodexSessionManager` in `session.py`
  - Thread ID mapping
  - Active turn tracking
  - Integration with `settings_manager` for persistence

### Phase 3: Event Handler
- [ ] Implement `CodexEventHandler` in `event_handler.py`
  - Notification dispatching
  - Agent message buffering (pending pattern)
  - Tool call formatting
  - Reasoning display
  - Error handling

### Phase 4: Agent Orchestration
- [ ] Implement `CodexAgent` in `codex/agent.py`
  - `handle_message`: transport → thread → turn flow
  - `handle_stop`: turn/interrupt
  - `clear_sessions`: thread/archive + transport cleanup
  - Approval request auto-approve
  - Thread resume on transport restart
- [ ] Update `modules/agents/registry.py` import
- [ ] Delete old `modules/agents/codex_agent.py`

### Phase 5: Testing & Polish
- [ ] Manual E2E test: start bot, send message, verify response
- [ ] Test message insertion (send second message while first is running)
- [ ] Test /stop (graceful interruption)
- [ ] Test /clear (session cleanup)
- [ ] Test transport crash recovery
- [ ] Lint check (ruff)
