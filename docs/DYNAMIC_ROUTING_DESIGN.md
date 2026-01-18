# Dynamic Channel Routing Design

## Overview

Replace static `agent_routes.yaml` with dynamic per-channel routing configuration that users can change via Slack menus.

## Requirements

1. **Backend selection**: Users can switch between `claude`, `codex`, `opencode` per channel
2. **OpenCode-specific options**: 
   - Agent selection (build, plan, etc.) - from `/agent` API
   - Model selection - from `/config/providers` API
3. **Claude/Codex**: No model selection (use their defaults)
4. **Fallback**: `agent_routes.yaml` remains as default; UI overrides are persisted in `~/.vibe_remote/state/settings.json`
5. **Entry points**:
   - Slack: `/start` button "Switch Agent" + `/settings` modal

## Data Structure

### UserSettings (in `~/.vibe_remote/state/settings.json`)

```python
@dataclass
class ChannelRouting:
    agent_backend: Optional[str] = None  # "claude" | "codex" | "opencode" | None (use default)
    opencode_agent: Optional[str] = None  # "build" | "plan" | ... | None (use OpenCode default)
    opencode_model: Optional[str] = None  # "provider/model" | None (use OpenCode default)

@dataclass
class UserSettings:
    hidden_message_types: List[str] = ...
    custom_cwd: Optional[str] = None
     channel_routing: Optional[ChannelRouting] = None

```

### JSON Representation

```json
{
  "C0A6U2GH6P5": {
    "hidden_message_types": ["system", "assistant", "user"],
    "custom_cwd": "/path/to/project",
    "channel_routing": {
      "agent_backend": "opencode",
      "opencode_agent": "build",
      "opencode_model": "anthropic/claude-opus-4-5"
    }
  }
}
```

## Routing Resolution Priority

```
1. channel_routing.agent_backend (from user_settings.json)
   ↓ if null
2. agent_routes.yaml overrides[channel_id]
   ↓ if not found
3. agent_routes.yaml platform.default
   ↓ if not found
4. agent_routes.yaml global default
   ↓ if not found
5. AgentService.default_agent ("claude")
```

## API Design

### Controller

```python
class Controller:
    def resolve_agent_for_context(self, context: MessageContext) -> str:
        """Unified agent resolution with dynamic override support."""
        settings_key = self._get_settings_key(context)
        
        # Check dynamic override first
        override = self.settings_manager.get_channel_routing(settings_key)
        if override and override.agent_backend:
            # Verify the agent is registered
            if override.agent_backend in self.agent_service.agents:
                return override.agent_backend
        
        # Fall back to static routing
        return self.agent_router.resolve(self.config.platform, settings_key)
    
    def get_opencode_overrides(self, context: MessageContext) -> Tuple[Optional[str], Optional[str]]:
        """Get OpenCode agent and model overrides for this channel."""
        settings_key = self._get_settings_key(context)
        routing = self.settings_manager.get_channel_routing(settings_key)
        if routing:
            return routing.opencode_agent, routing.opencode_model
        return None, None
```

### SettingsManager

```python
class SettingsManager:
    def get_channel_routing(self, settings_key: str) -> Optional[ChannelRouting]:
        """Get channel routing override."""
        settings = self.get_user_settings(settings_key)
        return settings.channel_routing
    
    def set_channel_routing(self, settings_key: str, routing: ChannelRouting):
        """Set channel routing override."""
        settings = self.get_user_settings(settings_key)
        settings.channel_routing = routing
        self.update_user_settings(settings_key, settings)
```

### OpenCodeServerManager

```python
class OpenCodeServerManager:
    async def get_available_agents(self, directory: str) -> List[Dict]:
        """Fetch available agents from OpenCode server."""
        # GET /agent with x-opencode-directory header
        
    async def get_available_models(self, directory: str) -> Dict:
        """Fetch available models from OpenCode server."""
        # GET /config/providers with x-opencode-directory header
        # Returns: { providers: [...], default: {...} }
    
    async def get_default_config(self, directory: str) -> Dict:
        """Fetch current default config from OpenCode server."""
        # GET /config with x-opencode-directory header
```

## Slack UI

### /start Button Layout (Updated)

```
Row 1: [📁 Current Dir] [📂 Change Work Dir]
Row 2: [🔄 Clear All Session] [⚙️ Settings]
Row 3: [🤖 Switch Agent]  # NEW
Row 4: [ℹ️ How it Works]
```

### Routing Modal (New)

```
┌─────────────────────────────────────────┐
│  🤖 Agent & Model Settings              │
├─────────────────────────────────────────┤
│  Current: OpenCode (build)              │
│                                         │
│  ┌─────────────────────────────────┐   │
│  │ Backend                          │   │
│  │ [▼ OpenCode                    ] │   │
│  └─────────────────────────────────┘   │
│                                         │
│  ── OpenCode Options ──────────────    │
│                                         │
│  ┌─────────────────────────────────┐   │
│  │ Agent                            │   │
│  │ [▼ build (default)             ] │   │
│  └─────────────────────────────────┘   │
│                                         │
│  ┌─────────────────────────────────┐   │
│  │ Model                            │   │
│  │ [▼ (Default) anthropic/claude..] │   │
│  └─────────────────────────────────┘   │
│                                         │
│  💡 Leave as default to use OpenCode's │
│     configured settings.                │
│                                         │
│  [Cancel]                    [Save]     │
└─────────────────────────────────────────┘
```

### Modal Blocks Structure

```python
view = {
    "type": "modal",
    "callback_id": "routing_modal",
    "title": {"type": "plain_text", "text": "Agent Settings"},
    "submit": {"type": "plain_text", "text": "Save"},
    "close": {"type": "plain_text", "text": "Cancel"},
    "private_metadata": channel_id,
    "blocks": [
        # Header with current status
        {"type": "section", "text": {"type": "mrkdwn", "text": "Current: ..."}},
        {"type": "divider"},
        
        # Backend select
        {
            "type": "input",
            "block_id": "backend_block",
            "element": {
                "type": "static_select",
                "action_id": "backend_select",
                "options": [
                    {"text": {"type": "plain_text", "text": "Claude Code"}, "value": "claude"},
                    {"text": {"type": "plain_text", "text": "Codex"}, "value": "codex"},
                    {"text": {"type": "plain_text", "text": "OpenCode"}, "value": "opencode"},
                ],
                "initial_option": {...}
            },
            "label": {"type": "plain_text", "text": "Backend"}
        },
        
        # OpenCode Agent select (conditional - shown via update)
        {
            "type": "input",
            "block_id": "opencode_agent_block",
            "optional": True,
            "element": {
                "type": "static_select",
                "action_id": "opencode_agent_select",
                "options": [...],  # From /agent API
            },
            "label": {"type": "plain_text", "text": "OpenCode Agent"}
        },
        
        # OpenCode Model select (conditional)
        {
            "type": "input", 
            "block_id": "opencode_model_block",
            "optional": True,
            "element": {
                "type": "static_select",  # or external_select if too many
                "action_id": "opencode_model_select",
                "options": [...],  # From /config/providers API
            },
            "label": {"type": "plain_text", "text": "Model"}
        },
        
        # Tip
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "💡 ..."}]}
    ]
}
```

## OpenCode Agent Message Flow

```
1. User sends message in Slack channel
2. MessageHandler.handle_user_message()
   → controller.resolve_agent_for_context(context) → "opencode"
   → controller.get_opencode_overrides(context) → ("build", "anthropic/claude-opus-4-5")
3. AgentRequest created with overrides attached
4. OpenCodeAgent.handle_message(request)
   → Uses request.opencode_agent, request.opencode_model instead of config defaults
5. OpenCodeServerManager.send_message(..., agent=override_agent, model=override_model)
```

## Implementation Steps

### Phase 1: Data & Routing
1. [ ] Add `ChannelRouting` dataclass to `settings_manager.py`
2. [ ] Add `channel_routing` field to `UserSettings`
3. [ ] Add `get_channel_routing()` and `set_channel_routing()` methods
4. [ ] Add `resolve_agent_for_context()` to Controller
5. [ ] Update all callers of `agent_router.resolve()` to use new method

### Phase 2: OpenCode Integration
6. [ ] Add `get_available_agents()` to OpenCodeServerManager
7. [ ] Add `get_available_models()` to OpenCodeServerManager  
8. [ ] Add `get_default_config()` to OpenCodeServerManager
9. [ ] Modify `OpenCodeAgent._process_message()` to use per-channel overrides

### Phase 3: Slack UI
10. [ ] Add "Switch Agent" button to `/start` command
11. [ ] Create `open_routing_modal()` in SlackBot
12. [ ] Handle `routing_modal` submission in `_handle_view_submission()`
13. [ ] Add `cmd_routing` callback handler

### Phase 4: Future App UI (Optional)
14. [ ] Add backend switching to the Vibe app UI
15. [ ] (Future) Add OpenCode agent/model selection to the Vibe app UI

## File Changes Summary

| File | Changes |
|------|---------|
| `modules/settings_manager.py` | Add `ChannelRouting`, routing methods |
| `core/controller.py` | Add `resolve_agent_for_context()`, `get_opencode_overrides()` |
| `core/handlers/message_handler.py` | Use new resolve method |
| `core/handlers/command_handlers.py` | Add Switch Agent button, routing callback |
| `core/handlers/settings_handler.py` | Add routing modal handler |
| `modules/im/slack.py` | Add `open_routing_modal()`, handle submission |
| `modules/agents/opencode_agent.py` | Add API methods, use per-channel overrides |
| `modules/agents/base.py` | Add opencode_agent/model fields to AgentRequest |

## Testing Checklist

- [ ] `/start` shows "Switch Agent" button
- [ ] Clicking button opens routing modal
- [ ] Modal shows only registered backends
- [ ] Selecting OpenCode shows agent/model dropdowns
- [ ] Selecting Claude/Codex hides OpenCode options
- [ ] Default options are marked and pre-selected
- [ ] Saving updates channel routing
- [ ] Messages route to selected backend
- [ ] OpenCode uses selected agent/model
- [ ] Clearing routing falls back to agent_routes.yaml
- [ ] Restart preserves routing settings
