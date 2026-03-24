"""WeChat QR code login flow for the vibe-remote setup wizard and CLI.

Ported from TypeScript: weixin-plugin-inspect/package/src/auth/login-qr.ts

Manages active login sessions in-memory (dict keyed by session_key).
Supports concurrent login sessions (for Web UI).
Auto-cleans expired sessions (5 min TTL).
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional

import aiohttp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Short poll timeout for Web UI: the UI polls every 2s, so each individual
# status check should return quickly.  The iLink server itself long-polls
# for ~30s, but we cut that short on the client side so the Flask
# _run_async wrapper (10s timeout) never kills us mid-request.
QR_POLL_TIMEOUT_S = 3


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class WeChatLoginSession:
    """Tracks one QR code login attempt."""

    session_key: str
    qrcode: str = ""  # opaque QR token from server
    qrcode_url: str = ""  # URL to render as QR image
    started_at: float = field(default_factory=time.time)
    status: str = "wait"  # "wait" | "scaned" | "confirmed" | "expired"
    bot_token: Optional[str] = None
    bot_id: Optional[str] = None
    base_url: Optional[str] = None
    user_id: Optional[str] = None
    qr_refresh_count: int = 1  # tracks how many QR codes have been issued


# ---------------------------------------------------------------------------
# Low-level API helpers
# ---------------------------------------------------------------------------
async def get_bot_qrcode(
    base_url: str,
    bot_type: str,
    *,
    timeout: float = 30,
) -> dict:
    """Fetch a new QR code from the iLink server.

    GET {base_url}/ilink/bot/get_bot_qrcode?bot_type={bot_type}

    Returns:
        dict with keys ``qrcode`` and ``qrcode_img_content``.
    """
    base = base_url.rstrip("/")
    url = f"{base}/ilink/bot/get_bot_qrcode?bot_type={bot_type}"
    logger.info("Fetching QR code from: %s", url)

    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                body = await resp.text()
                logger.error(
                    "QR code fetch failed: %s %s body=%s",
                    resp.status,
                    resp.reason,
                    body[:500],
                )
                raise RuntimeError(f"Failed to fetch QR code: {resp.status} {resp.reason}")
            # iLink API returns application/octet-stream, not application/json
            return await resp.json(content_type=None)


async def get_qrcode_status(
    base_url: str,
    qrcode: str,
    *,
    timeout: float = QR_POLL_TIMEOUT_S,
) -> dict:
    """Long-poll the QR code scan status.

    GET {base_url}/ilink/bot/get_qrcode_status?qrcode={qrcode}

    Returns:
        dict with at least ``status`` ("wait" | "scaned" | "confirmed" | "expired").
        On ``confirmed``, also includes ``bot_token``, ``ilink_bot_id``,
        ``baseurl``, and ``ilink_user_id``.
    """
    base = base_url.rstrip("/")
    url = f"{base}/ilink/bot/get_qrcode_status?qrcode={qrcode}"
    logger.debug("Long-poll QR status from: %s", url)

    headers = {"iLink-App-ClientVersion": "1"}
    client_timeout = aiohttp.ClientTimeout(total=timeout + 5)

    try:
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.get(url, headers=headers) as resp:
                raw = await resp.text()
                logger.debug("get_qrcode_status: HTTP %s body=%s", resp.status, raw[:200])
                if resp.status != 200:
                    logger.error(
                        "QR status poll failed: %s %s body=%s",
                        resp.status,
                        resp.reason,
                        raw[:500],
                    )
                    raise RuntimeError(f"Failed to poll QR status: {resp.status} {resp.reason}")
                import json

                return json.loads(raw)
    except asyncio.TimeoutError:
        logger.debug(
            "get_qrcode_status: client-side timeout after %ss, returning wait",
            timeout,
        )
        return {"status": "wait"}


# ---------------------------------------------------------------------------
# Auth manager
# ---------------------------------------------------------------------------
class WeChatAuthManager:
    """Manages QR code login sessions."""

    DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
    BOT_TYPE = "3"
    SESSION_TTL = 300  # 5 minutes
    MAX_QR_REFRESH = 3

    def __init__(self) -> None:
        self._sessions: Dict[str, WeChatLoginSession] = {}

    # -- helpers ----------------------------------------------------------

    def _is_session_fresh(self, session: WeChatLoginSession) -> bool:
        return (time.time() - session.started_at) < self.SESSION_TTL

    def cleanup_expired(self) -> None:
        """Remove sessions older than TTL."""
        expired_keys = [key for key, sess in self._sessions.items() if not self._is_session_fresh(sess)]
        for key in expired_keys:
            logger.debug("Cleaning up expired session: %s", key)
            del self._sessions[key]

    def get_session(self, session_key: str) -> Optional[WeChatLoginSession]:
        """Get current session state (for UI polling)."""
        self.cleanup_expired()
        return self._sessions.get(session_key)

    # -- public API -------------------------------------------------------

    async def start_login(
        self,
        session_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> dict:
        """Start a new QR code login.

        Returns:
            dict with ``session_key``, ``qrcode_url`` (may be empty on error),
            and ``message``.
        """
        session_key = session_key or str(uuid.uuid4())
        base_url = base_url or self.DEFAULT_BASE_URL

        self.cleanup_expired()

        # Reuse an existing fresh session if available
        existing = self._sessions.get(session_key)
        if existing and self._is_session_fresh(existing) and existing.qrcode_url:
            return {
                "session_key": session_key,
                "qrcode_url": existing.qrcode_url,
                "message": "QR code is ready. Please scan with WeChat.",
            }

        if not base_url:
            return {
                "session_key": session_key,
                "qrcode_url": "",
                "message": "No base URL configured. Please set the WeChat base URL before logging in.",
            }

        try:
            logger.info("Starting WeChat login with bot_type=%s", self.BOT_TYPE)
            qr_response = await get_bot_qrcode(base_url, self.BOT_TYPE)
            qrcode = qr_response.get("qrcode", "")
            qrcode_url = qr_response.get("qrcode_img_content", "")

            logger.info(
                "QR code received, qrcode=%s...%s img_url_len=%d",
                qrcode[:8] if len(qrcode) > 8 else qrcode,
                qrcode[-4:] if len(qrcode) > 4 else "",
                len(qrcode_url),
            )

            session = WeChatLoginSession(
                session_key=session_key,
                qrcode=qrcode,
                qrcode_url=qrcode_url,
                started_at=time.time(),
                status="wait",
                base_url=base_url,
            )
            self._sessions[session_key] = session

            return {
                "session_key": session_key,
                "qrcode_url": qrcode_url,
                "message": "Scan the QR code with WeChat to connect.",
            }
        except Exception as exc:
            logger.error("Failed to start WeChat login: %s", exc)
            return {
                "session_key": session_key,
                "qrcode_url": "",
                "message": f"Failed to start login: {exc}",
            }

    async def poll_status(self, session_key: str) -> dict:
        """Poll login status for a single cycle.

        Returns:
            dict with ``status``, ``message``, and on success also
            ``bot_token``, ``bot_id``, ``base_url``, ``user_id``.
        """
        session = self._sessions.get(session_key)
        if session is None:
            return {
                "status": "expired",
                "message": "Login session expired. Please start a new login.",
            }

        if not self._is_session_fresh(session):
            del self._sessions[session_key]
            return {
                "status": "expired",
                "message": "QR code has expired. Please start a new login.",
            }

        base_url = session.base_url or self.DEFAULT_BASE_URL

        try:
            status_resp = await get_qrcode_status(base_url, session.qrcode)
        except Exception as exc:
            # Don't delete session on transient errors (timeouts, network blips).
            # The UI will retry on the next poll cycle.
            logger.warning("Transient error polling QR status (session kept): %s", exc)
            return {
                "status": "wait",
                "message": "Waiting for QR code scan...",
            }

        status = status_resp.get("status", "wait")
        session.status = status
        logger.debug(
            "poll_status: session=%s status=%s has_bot_token=%s has_bot_id=%s",
            session_key,
            status,
            bool(status_resp.get("bot_token")),
            bool(status_resp.get("ilink_bot_id")),
        )

        if status == "wait":
            return {"status": "wait", "message": "Waiting for QR code scan..."}

        if status == "scaned":
            return {"status": "scaned", "message": "QR code scanned. Please confirm in WeChat."}

        if status == "expired":
            session.qr_refresh_count += 1
            if session.qr_refresh_count > self.MAX_QR_REFRESH:
                logger.warning(
                    "QR expired %d times, giving up session=%s",
                    self.MAX_QR_REFRESH,
                    session_key,
                )
                del self._sessions[session_key]
                return {
                    "status": "expired",
                    "message": "Login timed out: QR code expired multiple times. Please restart the login flow.",
                }

            logger.info(
                "QR expired, refreshing (%d/%d) session=%s",
                session.qr_refresh_count,
                self.MAX_QR_REFRESH,
                session_key,
            )
            try:
                qr_response = await get_bot_qrcode(base_url, self.BOT_TYPE)
                session.qrcode = qr_response.get("qrcode", "")
                session.qrcode_url = qr_response.get("qrcode_img_content", "")
                session.started_at = time.time()
                session.status = "wait"
                logger.info("New QR code obtained for session=%s", session_key)
                return {
                    "status": "refreshed",
                    "qrcode_url": session.qrcode_url,
                    "message": (
                        f"QR code expired, refreshed "
                        f"({session.qr_refresh_count}/{self.MAX_QR_REFRESH}). "
                        f"Please scan again."
                    ),
                }
            except Exception as exc:
                logger.error("Failed to refresh QR code: %s", exc)
                del self._sessions[session_key]
                return {
                    "status": "error",
                    "message": f"Failed to refresh QR code: {exc}",
                }

        if status == "confirmed":
            bot_id = status_resp.get("ilink_bot_id")
            if not bot_id:
                del self._sessions[session_key]
                logger.error("Login confirmed but ilink_bot_id missing from response")
                return {
                    "status": "error",
                    "message": "Login failed: server did not return ilink_bot_id.",
                }

            bot_token = status_resp.get("bot_token")
            resp_base_url = status_resp.get("baseurl")
            user_id = status_resp.get("ilink_user_id")

            # Store on session before removing
            session.bot_token = bot_token
            session.bot_id = bot_id
            session.base_url = resp_base_url
            session.user_id = user_id
            session.status = "confirmed"

            del self._sessions[session_key]

            logger.info(
                "Login confirmed! bot_id=%s user_id=%s...",
                bot_id,
                (user_id[:8] + "...") if user_id and len(user_id) > 8 else user_id,
            )
            return {
                "status": "confirmed",
                "bot_token": bot_token,
                "bot_id": bot_id,
                "base_url": resp_base_url,
                "user_id": user_id,
                "message": "Successfully connected to WeChat!",
            }

        # Unknown status fallback
        return {"status": status, "message": f"Unknown status: {status}"}

    async def wait_for_login(
        self,
        session_key: str,
        timeout_s: int = 480,
    ) -> dict:
        """Block until login completes or times out. Intended for CLI use.

        Returns:
            dict with ``status``, ``message``, and credentials on success.
        """
        deadline = time.time() + max(timeout_s, 1)

        while time.time() < deadline:
            result = await self.poll_status(session_key)
            status = result.get("status")

            if status in ("confirmed", "error"):
                return result

            if status == "expired" and "restart" in result.get("message", "").lower():
                # Max refreshes exhausted
                return result

            # For "wait", "scaned", "refreshed" — keep polling
            await asyncio.sleep(1)

        # Timed out
        if session_key in self._sessions:
            del self._sessions[session_key]
        logger.warning(
            "wait_for_login: timed out after %ds session=%s",
            timeout_s,
            session_key,
        )
        return {
            "status": "expired",
            "message": "Login timed out. Please try again.",
        }
