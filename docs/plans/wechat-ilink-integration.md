# WeChat iLink Bot Integration Plan

## Background

On 2026-03-21, Tencent officially published `@tencent-weixin/openclaw-weixin` -- a WeChat channel plugin for OpenClaw. This is the first time WeChat has exposed a **personal WeChat** messaging API to third-party agent frameworks. The plugin is MIT licensed, with full TypeScript source code published to npm (41 files, ~166KB).

The underlying protocol is a simple **HTTP JSON API** (internally called "iLink bot") that supports:
- Long-poll message receiving (like Telegram Bot API's `getUpdates`)
- Message sending (text, image, video, file, voice)
- CDN-based media transfer with AES-128-ECB encryption
- WeChat OAuth2 QR-code login

This plan outlines how to integrate WeChat as a new IM platform in vibe-remote.

## Goal

Add WeChat (personal) as a supported IM platform in vibe-remote, allowing users to interact with AI agents via WeChat messages.

## Protocol Summary

### Authentication
1. Call `GET /ilink/bot/get_bot_qrcode?bot_type=3` to get a QR code URL
2. User scans QR with WeChat phone app and confirms
3. Poll `GET /ilink/bot/get_qrcode_status?qrcode={qrcode}` until `confirmed`
4. Receive `bot_token` + `ilink_bot_id` + `ilink_user_id`
5. All subsequent requests use `Authorization: Bearer <bot_token>`

### Core API Endpoints (all POST, JSON)

| Endpoint | Purpose |
|----------|---------|
| `ilink/bot/getupdates` | Long-poll for new messages |
| `ilink/bot/sendmessage` | Send message (text/media) |
| `ilink/bot/getuploadurl` | Get CDN pre-signed upload URL |
| `ilink/bot/getconfig` | Get account config (typing ticket) |
| `ilink/bot/sendtyping` | Send typing indicator |

### Request Headers
```
Content-Type: application/json
AuthorizationType: ilink_bot_token
Authorization: Bearer <bot_token>
X-WECHAT-UIN: <random uint32 base64>
```

### Message Structure
- `WeixinMessage`: contains `from_user_id`, `to_user_id`, `context_token`, `item_list`
- `MessageItem` types: TEXT(1), IMAGE(2), VOICE(3), FILE(4), VIDEO(5)
- Media via CDN with AES-128-ECB encryption per-file

### Key Protocol Detail: `context_token`
Every inbound message carries a `context_token`. This token **must** be included when replying to that user. It is ephemeral and not persisted by the server -- we must cache it in-memory per `(account_id, user_id)` pair.

## Solution Design

### Architecture

```
WeChat Server (ilinkai.weixin.qq.com)
    |
    | HTTP JSON (long-poll + POST)
    |
modules/im/wechat.py  (WeChatBot : BaseIMClient)
    |
    |-- src: wechat_api.py     (API client: getUpdates, sendMessage, etc.)
    |-- src: wechat_cdn.py     (AES-128-ECB encrypt/decrypt + CDN upload/download)
    |-- src: wechat_auth.py    (QR code login flow)
    |
core/controller.py  (standard wiring)
    |
core/handlers/  (platform-agnostic business logic)
```

### Files to Create

| File | Purpose | Estimated Lines |
|------|---------|----------------|
| `modules/im/wechat.py` | Main adapter (`WeChatBot(BaseIMClient)`) | ~600 |
| `modules/im/wechat_api.py` | HTTP API client (5 endpoints + types) | ~300 |
| `modules/im/wechat_cdn.py` | AES-128-ECB + CDN upload/download | ~200 |
| `modules/im/wechat_auth.py` | QR code login flow | ~150 |
| `modules/im/formatters/wechat_formatter.py` | Markdown formatter | ~60 |

### Files to Modify

| File | Change |
|------|--------|
| `config/v2_config.py` | Add `WeChatConfig` dataclass + `wechat` field in `V2Config` |
| `modules/im/factory.py` | Add `"wechat"` branch in `create_client()`, `validate_platform_config()`, `get_supported_platforms()` |
| `core/controller.py` | Add `"wechat"` branches in `_init_modules()` and `_refresh_config_from_disk()` |
| `modules/im/formatters/__init__.py` | Export `WeChatFormatter` |
| `ui/src/i18n/en.json` + `zh.json` | WeChat platform labels |

### Key Design Decisions

#### 1. Long-Poll vs WebSocket
The existing adapters all use WebSocket. WeChat uses HTTP long-poll. This is simpler -- just an async loop:

```python
async def _poll_loop(self):
    sync_buf = self._load_sync_buf()
    while not self._stop_event.is_set():
        try:
            resp = await self._api.get_updates(sync_buf)
            if resp.get("get_updates_buf"):
                sync_buf = resp["get_updates_buf"]
                self._save_sync_buf(sync_buf)
            for msg in resp.get("msgs", []):
                await self._process_message(msg)
        except Exception as e:
            logger.error(f"Poll error: {e}")
            await asyncio.sleep(3)
```

#### 2. context_token Management
Store in-memory dict `{(account_id, user_id): context_token}`, updated on every inbound message, read on every outbound send. Lost on restart (acceptable -- next inbound message repopulates).

#### 3. CDN Media Encryption
Use Python `cryptography` library for AES-128-ECB:

```python
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding

def aes_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    return cipher.encryptor().update(padded) + cipher.encryptor().finalize()
```

#### 4. QR Code Login in Web UI
Two options:
- **Phase 1 (MVP)**: CLI-only login via `vibe` command, display QR in terminal
- **Phase 2**: Add QR code display to Web UI setup wizard (new API endpoint that returns QR URL, frontend renders it)

#### 5. WeChat Has No Threads
WeChat personal chat is flat (no threading). Set:
- `should_use_thread_for_reply()` -> `False`
- `should_use_thread_for_dm_session()` -> `False`

#### 6. WeChat Has No Buttons/Callbacks
WeChat personal messages don't support inline buttons. Implement:
- `send_message_with_buttons()` -> send text only, append button labels as text hints
- `answer_callback()` -> no-op return `False`
- `edit_message()` -> no-op return `False` (WeChat doesn't support message editing)

### Config Schema

```python
@dataclass
class WeChatConfig(BaseIMConfig):
    bot_token: str = ""
    base_url: str = "https://ilinkai.weixin.qq.com"
    cdn_base_url: str = "https://novac2c.cdn.weixin.qq.com/c2c"
    route_tag: str = ""

    def validate(self) -> None:
        # bot_token can be empty during setup wizard flow
        pass
```

## Implementation Order

### Phase 1: Core (MVP)
1. `wechat_api.py` -- HTTP client for all 5 API endpoints
2. `wechat_auth.py` -- QR code login flow
3. `wechat.py` -- Main adapter with long-poll loop + send_message
4. `wechat_formatter.py` -- Basic markdown-to-plaintext
5. Config + factory + controller wiring
6. Manual test: send/receive text messages via WeChat

### Phase 2: Media
7. `wechat_cdn.py` -- AES-128-ECB encrypt/decrypt + CDN upload/download
8. Inbound media download (images, files, voice, video)
9. Outbound media upload (images, files)
10. Voice message: SILK decode (optional, can skip initially)

### Phase 3: Polish
11. Web UI integration (QR login in setup wizard)
12. Multi-account support
13. Typing indicator support
14. Sync buf persistence (resume from last message after restart)
15. Three-end regression: add WeChat as 4th platform

## Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Protocol instability (API just launched 2026-03-21) | High | Pin to known-working version, add version header, monitor for changes |
| Invite code / whitelist restriction | Medium | May need Tencent approval; test with actual QR login first |
| Rate limiting | Medium | Implement exponential backoff in poll loop |
| Session timeout (errcode -14) | Low | Handle gracefully, re-auth on next QR scan |
| AES-ECB dual key encoding (base64-of-raw vs base64-of-hex) | Low | Port the detection logic from TypeScript source |

## Dependencies

New Python dependencies:
- `cryptography` (likely already available) -- for AES-128-ECB
- `qrcode` (optional) -- for terminal QR rendering during CLI login

## Reference Material

- npm: `@tencent-weixin/openclaw-weixin@1.0.2` (MIT, full TypeScript source)
- npm: `@tencent-weixin/openclaw-weixin-cli@1.0.2` (MIT, CLI wrapper)
- GitHub: `photon-hq/qclaw-wechat-client` (reverse-engineered QClaw desktop client, includes AGP WebSocket protocol)
- API base: `https://ilinkai.weixin.qq.com`
- CDN base: `https://novac2c.cdn.weixin.qq.com/c2c`
