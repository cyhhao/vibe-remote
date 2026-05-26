"""``httpx`` wrapper for talking to the controller's internal Unix socket.

C5 of Plan 2 (see ``docs/plans/workbench-dispatch-architecture.md``).
The UI server runs as its own subprocess; this module is how it reaches
``core.internal_server`` to trigger ``dispatch_turn`` and stream the
agent's SSE chunked response back to the browser.

Single responsibility: keep all the socket-path / httpx-transport /
SSE-parsing boilerplate out of the UI route bodies. Routes call
``stream_dispatch(...)`` and either iterate the resulting async
generator straight into a ``StreamingResponse``, or catch
``InternalServerUnavailable`` to fall back to the N1 queue path.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import httpx

from config import paths

logger = logging.getLogger(__name__)


class InternalServerUnavailable(Exception):
    """Raised when the dispatch socket cannot be reached.

    Routes should catch this and degrade to the queue-based fallback so
    a controller crash or socket-bind race doesn't take down the
    user-facing send-compose flow.
    """


def default_socket_path() -> Path:
    """Mirror ``core.internal_server.default_socket_path`` without an
    import cycle.

    ``core.internal_server`` lives in the controller process and we
    deliberately don't import controller-side modules from the UI
    server. Duplicating the one-line path-derivation keeps the
    boundaries clean.
    """

    return paths.get_state_dir() / "dispatch.sock"


async def stream_dispatch(
    payload: dict[str, Any],
    *,
    socket_path: Optional[Path] = None,
    timeout: float = 1800.0,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Send a dispatch request to the controller and yield SSE events.

    Each yielded tuple is ``(event_name, parsed_data)`` — e.g.
    ``("turn.start", {...})``, ``("turn.chunk", {...})``, ``("turn.end",
    {...})``. The caller is expected to re-encode them for the browser
    (UI server adds the ``data: {...}\\n\\n`` SSE framing back on the way
    out so this layer can stay format-agnostic).

    Raises ``InternalServerUnavailable`` for connect-time failures so
    the caller can fall back to N1 (``agent_runs`` queue).
    """

    target = (socket_path or default_socket_path()).expanduser().resolve()
    if not target.exists():
        raise InternalServerUnavailable(f"dispatch socket missing at {target}")

    transport = httpx.AsyncHTTPTransport(uds=str(target))
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://localhost",
            timeout=httpx.Timeout(timeout, connect=5.0),
        ) as client:
            try:
                stream = client.stream("POST", "/internal/dispatch", json=payload)
            except (httpx.ConnectError, FileNotFoundError, PermissionError) as exc:
                raise InternalServerUnavailable(str(exc)) from exc

            async with stream as resp:
                if resp.status_code >= 400:
                    detail = await resp.aread()
                    raise InternalServerUnavailable(
                        f"dispatch endpoint returned {resp.status_code}: {detail!r}"
                    )

                current_event: Optional[str] = None
                async for line in resp.aiter_lines():
                    if not line:
                        # Blank line ends an SSE event block; reset the
                        # event-name buffer so a missing ``event:`` field
                        # on the next block defaults to ``message``.
                        current_event = None
                        continue
                    if line.startswith("event:"):
                        current_event = line.split(":", 1)[1].strip()
                    elif line.startswith("data:"):
                        raw = line[5:].lstrip()
                        try:
                            parsed = json.loads(raw)
                        except json.JSONDecodeError:
                            logger.warning("internal_client: invalid SSE data line %r", raw)
                            continue
                        yield (current_event or "message", parsed)
    except InternalServerUnavailable:
        raise
    except (httpx.ConnectError, FileNotFoundError, PermissionError) as exc:
        raise InternalServerUnavailable(str(exc)) from exc


async def health(socket_path: Optional[Path] = None) -> bool:
    """Probe ``GET /internal/health``. Returns False on any failure.

    Useful for UI startup checks and for the fallback decision in the
    streaming route body so we can decline cleanly before opening the
    longer-lived dispatch stream.
    """

    target = (socket_path or default_socket_path()).expanduser().resolve()
    if not target.exists():
        return False
    transport = httpx.AsyncHTTPTransport(uds=str(target))
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://localhost",
            timeout=httpx.Timeout(2.0, connect=1.0),
        ) as client:
            resp = await client.get("/internal/health")
            return resp.status_code == 200 and (resp.json() or {}).get("ok") is True
    except Exception:
        return False
