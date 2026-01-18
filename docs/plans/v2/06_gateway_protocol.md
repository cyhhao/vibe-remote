# Gateway Protocol (V2)

This document defines the minimal relay protocol between the local gateway and the cloud (SaaS mode). The protocol must support low-latency delivery, reconnect safety, and workspace isolation.

## Transport

- Preferred: WebSocket (gateway initiates outbound connection).
- Alternative: gRPC streaming (compatible envelope below).
- All messages are JSON objects.

## Authentication

Gateway connects with workspace-scoped credentials.

- `workspace_token`: issued after OAuth install.
- `client_id`: locally generated unique id.

Handshake payload:

```
{
  "type": "hello",
  "workspace_token": "...",
  "client_id": "...",
  "client_version": "v2",
  "capabilities": ["events", "commands", "files"]
}
```

Server responds:

```
{
  "type": "hello_ack",
  "status": "ok",
  "server_time": "2026-01-18T12:00:00Z",
  "heartbeat_interval": 25
}
```

## Message Envelope

All payloads use a shared envelope to enable retry, tracing, and ordering.

Common fields:

- `id`: unique message id (uuid)
- `type`: `event` | `command` | `response` | `error` | `ack` | `heartbeat`
- `workspace_id`: Slack team id
- `channel_id`: Slack channel id
- `thread_id`: Slack thread id (optional)
- `user_id`: Slack user id
- `timestamp`: ISO 8601
- `payload`: event-specific body
- `trace_id`: optional run trace id

Example inbound event:

```
{
  "id": "...",
  "type": "event",
  "workspace_id": "T123",
  "channel_id": "C456",
  "thread_id": "123.456",
  "user_id": "U789",
  "timestamp": "2026-01-18T12:00:01Z",
  "payload": {
    "event_type": "message",
    "text": "hello",
    "message_id": "1700000000.0001"
  }
}
```

## ACK + Retry

- The gateway must ACK every `event` with `ack` containing the same `id`.
- Server retries if no ACK within `ack_timeout` (default 5s).
- `retry_count` is included in retried messages.

ACK example:

```
{
  "type": "ack",
  "id": "...",
  "timestamp": "2026-01-18T12:00:02Z"
}
```

## Responses (Gateway -> Cloud)

Gateway responses return execution output to cloud for Slack posting.

```
{
  "id": "...",
  "type": "response",
  "workspace_id": "T123",
  "channel_id": "C456",
  "thread_id": "123.456",
  "user_id": "U789",
  "timestamp": "2026-01-18T12:00:05Z",
  "payload": {
    "text": "...",
    "blocks": [],
    "attachments": [],
    "status": "ok"
  },
  "trace_id": "run-..."
}
```

## Heartbeats

- Gateway sends `heartbeat` every `heartbeat_interval` seconds.
- Server responds with `heartbeat_ack`.

```
{"type": "heartbeat", "timestamp": "..."}
```

## Error Handling

```
{
  "type": "error",
  "id": "...",
  "timestamp": "...",
  "payload": {
    "code": "invalid_auth",
    "message": "..."
  }
}
```

## Reconnect Behavior

- Gateway reconnects with exponential backoff.
- After reconnect, gateway may send `resume` with last seen message id.
- Server can re-deliver missed events by id.

```
{
  "type": "resume",
  "last_event_id": "..."
}
```

## Security Notes

- Only workspace-scoped tokens are used.
- No Slack app-level tokens are exposed to the gateway.
- Cloud stores minimal metadata (workspace binding + connection status).
