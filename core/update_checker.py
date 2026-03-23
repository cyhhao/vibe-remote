"""
Automatic update checker and installer.

This module provides:
1. Periodic checking for new versions on PyPI
2. Slack notifications to workspace owner when updates are available
3. Automatic update installation when the system is idle
"""

import asyncio
import calendar
import json
import logging
import tempfile
import time
import urllib.request
from dataclasses import dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

from config import paths
from config.v2_config import UpdateConfig
from config.v2_settings import _infer_channel_platform, _infer_user_platform, _split_scoped_key
from modules.im import MessageContext

if TYPE_CHECKING:
    from core.controller import Controller

logger = logging.getLogger(__name__)

# Action ID for the update button in Slack
UPDATE_BUTTON_ACTION_ID = "vibe_update_now"

# Minimum check interval to prevent tight loops (in minutes)
MIN_CHECK_INTERVAL_MINUTES = 1

# Grace period after sending an update notification before auto-update can proceed (in minutes).
# This gives admins time to read the notification and decide whether to update manually.
NOTIFICATION_GRACE_PERIOD_MINUTES = 10


def _compare_versions(latest: str, current: str) -> bool:
    """Compare versions using packaging.version for PEP440 compliance."""
    try:
        from packaging.version import Version

        return Version(latest) > Version(current)
    except Exception:
        # Fallback: simple comparison if packaging not available or version invalid
        try:
            latest_parts = [int(x) for x in latest.split(".")[:3] if x.isdigit()]
            current_parts = [int(x) for x in current.split(".")[:3] if x.isdigit()]
            return latest_parts > current_parts
        except (ValueError, AttributeError):
            return latest != current


def _fetch_pypi_version_sync() -> Dict[str, Any]:
    """Synchronous PyPI version fetch (to be run in thread)."""
    from vibe import __version__

    current = __version__
    result = {"current": current, "latest": None, "has_update": False, "error": None}

    try:
        url = "https://pypi.org/pypi/vibe-remote/json"
        req = urllib.request.Request(url, headers={"User-Agent": "vibe-remote"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            latest = data.get("info", {}).get("version", "")
            result["latest"] = latest

            if latest and latest != current:
                result["has_update"] = _compare_versions(latest, current)
    except Exception as e:
        result["error"] = str(e)

    return result


@dataclass
class UpdateState:
    """Persistent state for update tracking."""

    notified_version: Optional[str] = None
    notified_at: Optional[str] = None
    last_check_at: Optional[str] = None
    last_activity_at: Optional[float] = None

    @classmethod
    def load(cls) -> "UpdateState":
        path = cls._get_path()
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                notified_version=data.get("notified_version"),
                notified_at=data.get("notified_at"),
                last_check_at=data.get("last_check_at"),
                last_activity_at=data.get("last_activity_at"),
            )
        except Exception as e:
            logger.warning(f"Failed to load update state: {e}")
            return cls()

    def save(self) -> None:
        """Save state atomically using temp file + rename."""
        try:
            path = self._get_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "notified_version": self.notified_version,
                "notified_at": self.notified_at,
                "last_check_at": self.last_check_at,
                "last_activity_at": self.last_activity_at,
            }
            # Atomic write: write to temp file, then rename
            with tempfile.NamedTemporaryFile(
                mode="w", dir=path.parent, suffix=".tmp", delete=False, encoding="utf-8"
            ) as f:
                json.dump(data, f, indent=2)
                temp_path = Path(f.name)
            temp_path.replace(path)
        except Exception as e:
            logger.warning(f"Failed to save update state: {e}")

    @staticmethod
    def _get_path() -> Path:
        return paths.get_state_dir() / "update_state.json"


class UpdateChecker:
    """Handles automatic update checking and installation."""

    def __init__(self, controller: "Controller", config: UpdateConfig):
        self.controller = controller
        self.config = config
        self.state = UpdateState.load()
        self._check_task: Optional[asyncio.Task] = None
        self._running = False
        self._upgrade_lock = asyncio.Lock()  # Prevent concurrent upgrades
        self._cached_owner_dm_channel: Optional[str] = None  # Cache DM channel ID (legacy fallback)

    def start(self) -> None:
        """Start the periodic update checker."""
        if self._running:
            return
        if self.config.check_interval_minutes <= 0:
            logger.info("Update checker disabled (check_interval_minutes=0)")
            return

        # Initialize last_activity_at if not set (for idle detection baseline)
        if not self.state.last_activity_at:
            self.state.last_activity_at = time.time()
            self.state.save()

        self._running = True
        self._check_task = asyncio.create_task(self._check_loop())
        logger.info(
            f"Update checker started (interval={self.config.check_interval_minutes}min, "
            f"auto_update={self.config.auto_update}, idle_minutes={self.config.idle_minutes})"
        )

    def stop(self) -> None:
        """Stop the periodic update checker."""
        self._running = False
        if self._check_task:
            self._check_task.cancel()
            self._check_task = None

    def record_activity(self) -> None:
        """Record user activity (called when a Slack message is received)."""
        self.state.last_activity_at = time.time()
        self.state.save()  # save() has its own try/except, won't raise

    def _reload_config(self) -> None:
        """Reload UpdateConfig from config file (for hot-reload support)."""
        try:
            config_path = paths.get_config_path()
            if config_path.exists():
                data = json.loads(config_path.read_text(encoding="utf-8"))
                update_data = data.get("update") or {}
                # Backward compat: rename legacy "notify_slack" → "notify_admins"
                if "notify_slack" in update_data and "notify_admins" not in update_data:
                    update_data["notify_admins"] = update_data.pop("notify_slack")
                valid = {f.name for f in fields(UpdateConfig)}
                self.config = UpdateConfig(**{k: v for k, v in update_data.items() if k in valid})
        except Exception as e:
            logger.warning(f"Failed to reload update config: {e}")

    async def _check_loop(self) -> None:
        """Main loop for periodic update checking."""
        # Initial delay to let the service fully start
        await asyncio.sleep(30)

        while self._running:
            try:
                await self._do_check()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Update check failed: {e}", exc_info=True)

            # Reload config and get interval (with minimum bound to prevent tight loop)
            self._reload_config()
            interval = max(self.config.check_interval_minutes, MIN_CHECK_INTERVAL_MINUTES)

            # If interval is set to 0 (disabled), keep loop alive so hot-reload can re-enable
            await asyncio.sleep(interval * 60)

    async def _do_check(self) -> None:
        """Perform a single update check."""
        try:
            # Reload config for hot-reload support (e.g., user toggled auto_update in UI)
            self._reload_config()

            # Skip if disabled
            if self.config.check_interval_minutes <= 0:
                return

            # Fetch version info in a thread to avoid blocking the event loop
            version_info = await asyncio.to_thread(_fetch_pypi_version_sync)

            self.state.last_check_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self.state.save()

            if version_info.get("error"):
                logger.warning(f"Failed to check for updates: {version_info['error']}")
                return

            if not version_info.get("has_update"):
                logger.debug(f"No update available (current={version_info['current']})")
                return

            latest = version_info["latest"]
            current = version_info["current"]
            logger.info(f"Update available: {current} -> {latest}")

            # Notification flow — failure must not block auto-update
            if self.config.notify_admins and self.state.notified_version != latest:
                try:
                    await self._send_update_notification(current, latest)
                    # Only record delivery time on success — grace period depends on this
                    self.state.notified_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                except Exception as e:
                    logger.error(f"Failed to send update notification: {e}", exc_info=True)
                # Always mark version to prevent retry every cycle (even if send failed)
                self.state.notified_version = latest
                self.state.save()

            # Auto-update flow — respect a grace period after successful notification
            # so the admin has time to read the notification before auto-update kicks in.
            if self.config.auto_update and self._is_idle():
                if self._within_notification_grace_period(latest):
                    logger.info("Within notification grace period, deferring auto-update")
                else:
                    logger.info("System is idle, performing auto-update...")
                    await self._perform_update(latest)
        except Exception as e:
            logger.error(f"Update check failed: {e}", exc_info=True)

    async def _get_version_info_async(self) -> Dict[str, Any]:
        """Get version info asynchronously."""
        return await asyncio.to_thread(_fetch_pypi_version_sync)

    def _is_idle(self) -> bool:
        """Check if the system is idle (no active sessions and no recent activity)."""
        # Check for active agent sessions
        if self._has_active_sessions():
            logger.debug("Not idle: has active sessions")
            return False

        # Check for recent activity
        # If no activity recorded yet, consider it NOT idle (just started)
        if not self.state.last_activity_at:
            logger.debug("Not idle: no activity recorded yet (service just started)")
            return False

        idle_seconds = time.time() - self.state.last_activity_at
        idle_minutes = idle_seconds / 60
        if idle_minutes < self.config.idle_minutes:
            logger.debug(f"Not idle: last activity {idle_minutes:.1f} minutes ago")
            return False

        return True

    def _within_notification_grace_period(self, target_version: str) -> bool:
        """Check if we're still within the grace period after sending an update notification.

        Returns True if a notification for *this version* was successfully delivered
        recently and we should defer auto-update to give the admin time to read it.
        """
        if not self.state.notified_at:
            return False
        # Only apply grace for the version we actually notified about
        if self.state.notified_version != target_version:
            return False
        try:
            notified_ts = calendar.timegm(time.strptime(self.state.notified_at, "%Y-%m-%dT%H:%M:%SZ"))
            elapsed_minutes = (time.time() - notified_ts) / 60
            return elapsed_minutes < NOTIFICATION_GRACE_PERIOD_MINUTES
        except (ValueError, OverflowError) as e:
            logger.warning(f"Failed to parse notified_at '{self.state.notified_at}': {e}")
            return False

    def _has_active_sessions(self) -> bool:
        """Check if any agent has active sessions."""
        try:
            # Check OpenCode active polls
            if hasattr(self.controller, "sessions"):
                active_polls = self.controller.sessions.get_all_active_polls()
                if active_polls:
                    return True

            # Check Claude sessions
            if hasattr(self.controller, "claude_sessions") and self.controller.claude_sessions:
                return True

            # Check Codex active processes
            if hasattr(self.controller, "agent_service"):
                codex = self.controller.agent_service.agents.get("codex")
                if codex and hasattr(codex, "active_processes") and codex.active_processes:
                    return True
        except Exception as e:
            logger.warning(f"Error checking active sessions: {e}")

        return False

    def _get_admin_user_ids(self) -> list:
        """Get admin user IDs from the settings store.

        Returns scoped admin user IDs across all enabled platforms.
        """
        try:
            if hasattr(self.controller, "settings_manager"):
                store = self.controller.settings_manager.get_store()
                if store:
                    return list(store.get_admins().keys())
        except Exception as e:
            logger.warning(f"Failed to get admin user IDs: {e}")
        return []

    def _get_im_client_for_platform(self, platform: str):
        im_clients = getattr(self.controller, "im_clients", None)
        if isinstance(im_clients, dict) and platform in im_clients:
            return im_clients[platform]
        return self.controller.im_client

    def _get_im_client_for_user(self, user_id: str):
        scoped_platform, raw_user_id = _split_scoped_key(str(user_id))
        platform = scoped_platform or _infer_user_platform(raw_user_id)
        return self._get_im_client_for_platform(platform), raw_user_id, platform

    async def _get_workspace_owner_id(self) -> Optional[str]:
        """Get the Slack workspace primary owner's user ID.

        Legacy fallback: used only when no admins are configured.
        """
        try:
            im_client = self._get_im_client_for_platform("slack")
            if not im_client or not hasattr(im_client, "web_client"):
                return None

            # Paginate through users to handle large workspaces
            cursor = None
            while True:
                kwargs = {"limit": 200}
                if cursor:
                    kwargs["cursor"] = cursor

                response = await im_client.web_client.users_list(**kwargs)
                if not response.get("ok"):
                    return None

                for member in response.get("members", []):
                    if member.get("is_primary_owner"):
                        return member.get("id")

                # Check for next page
                cursor = response.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break

            # Second pass: fallback to any owner if no primary owner found
            cursor = None
            while True:
                kwargs = {"limit": 200}
                if cursor:
                    kwargs["cursor"] = cursor

                response = await im_client.web_client.users_list(**kwargs)
                if not response.get("ok"):
                    return None

                for member in response.get("members", []):
                    if member.get("is_owner"):
                        return member.get("id")

                cursor = response.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break

        except Exception as e:
            logger.warning(f"Failed to get workspace owner: {e}")

        return None

    async def _open_dm_channel(self, user_id: str) -> Optional[str]:
        """Open a DM channel with a user and return the channel ID."""
        # Use cached channel if available
        if self._cached_owner_dm_channel:
            return self._cached_owner_dm_channel

        try:
            im_client = self._get_im_client_for_platform("slack")
            if not im_client or not hasattr(im_client, "web_client"):
                return None

            response = await im_client.web_client.conversations_open(users=[user_id])
            if response.get("ok"):
                channel_id = response.get("channel", {}).get("id")
                self._cached_owner_dm_channel = channel_id
                return channel_id
        except Exception as e:
            logger.warning(f"Failed to open DM channel with user {user_id}: {e}")

        return None

    async def _send_update_notification(self, current: str, latest: str) -> None:
        """Send update notification to admin users, with platform-specific fallbacks."""
        platform = getattr(self.controller.config, "platform", "slack")
        admin_ids = self._get_admin_user_ids()

        if admin_ids:
            # Send DM to each admin via the platform-agnostic send_dm method
            await self._send_notification_to_admins(admin_ids, current, latest, platform)
        else:
            # Legacy fallback: no admins configured
            if platform == "slack":
                await self._send_slack_notification_legacy(current, latest)
            elif platform == "discord":
                await self._send_discord_notification_fallback(current, latest)
            else:
                logger.warning("Update notification skipped: no admins and unsupported platform %s", platform)

    async def _send_notification_to_admins(self, admin_ids: list, current: str, latest: str, platform: str) -> None:
        """Send update notification DM to each admin user."""
        for uid in admin_ids:
            im_client, raw_user_id, user_platform = self._get_im_client_for_user(uid)
            try:
                if user_platform == "slack":
                    text = f"Vibe Remote update available: {current} → {latest}"
                    blocks = [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f":rocket: *Vibe Remote Update Available*\n\n"
                                f"A new version is available: `{current}` → `{latest}`",
                            },
                        },
                        {
                            "type": "actions",
                            "elements": [
                                {
                                    "type": "button",
                                    "text": {"type": "plain_text", "text": "Update Now", "emoji": True},
                                    "style": "primary",
                                    "action_id": UPDATE_BUTTON_ACTION_ID,
                                    "value": latest,
                                }
                            ],
                        },
                    ]
                    result = await im_client.send_dm(raw_user_id, text, blocks=blocks)
                elif user_platform == "discord":
                    text = f"🚀 **Vibe Remote Update Available**\n\nUpdate from `{current}` → `{latest}`"
                    result = await im_client.send_dm(raw_user_id, text)
                else:
                    text = f"🚀 Vibe Remote Update Available\n\nUpdate from {current} → {latest}"
                    result = await im_client.send_dm(raw_user_id, text)

                if result:
                    logger.info(f"Sent update notification to admin {uid}")
                else:
                    logger.warning(f"Failed to send update notification to admin {uid}: send_dm returned None")
            except Exception as e:
                logger.error(f"Failed to send update notification to admin {uid}: {e}")

    async def _send_slack_notification_legacy(self, current: str, latest: str) -> None:
        """Legacy Slack notification: send to workspace owner when no admins configured."""
        owner_id = await self._get_workspace_owner_id()
        if not owner_id:
            logger.warning("Cannot send update notification: no admins and no workspace owner found")
            return

        # Open DM channel first (required for sending messages to users)
        dm_channel = await self._open_dm_channel(owner_id)
        if not dm_channel:
            logger.warning(f"Cannot send update notification: failed to open DM with {owner_id}")
            return

        try:
            im_client = self._get_im_client_for_platform("slack")
            blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":rocket: *Vibe Remote Update Available*\n\n"
                        f"A new version is available: `{current}` → `{latest}`",
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Update Now", "emoji": True},
                            "style": "primary",
                            "action_id": UPDATE_BUTTON_ACTION_ID,
                            "value": latest,
                        }
                    ],
                },
            ]

            await im_client.web_client.chat_postMessage(
                channel=dm_channel, text=f"Vibe Remote update available: {current} → {latest}", blocks=blocks
            )
            logger.info(f"Sent update notification to workspace owner {owner_id}")
        except Exception as e:
            logger.error(f"Failed to send update notification: {e}")

    async def _send_discord_notification_fallback(self, current: str, latest: str) -> None:
        """Fallback Discord notification: send to first enabled channel when no admins configured."""
        channel_id = self._get_default_notification_channel_id()
        if not channel_id:
            logger.warning("Cannot send update notification: no admins and no enabled channel found")
            return
        try:
            from modules.im import InlineButton, InlineKeyboard, MessageContext

            text = f"🚀 **Vibe Remote Update Available**\n\nUpdate from `{current}` → `{latest}`"
            keyboard = InlineKeyboard(
                buttons=[[InlineButton(text="Update Now", callback_data=f"vibe_update_now:{latest}")]]
            )
            context = MessageContext(user_id="system", channel_id=channel_id, platform="discord")
            await self.controller.get_im_client_for_context(context).send_message_with_buttons(
                context, text, keyboard, parse_mode="markdown"
            )
            logger.info("Sent update notification to Discord channel %s", channel_id)
        except Exception as e:
            logger.error(f"Failed to send Discord update notification: {e}")

    def _get_default_notification_channel_id(self) -> Optional[str]:
        if not hasattr(self.controller, "settings_manager"):
            return None
        store = self.controller.settings_manager.get_store()
        if store is None:
            return None
        platform = getattr(self.controller.config, "platform", "slack")
        for channel_id, settings in store.get_channels_for_platform(platform).items():
            if getattr(settings, "enabled", False):
                if platform == "discord" and not str(channel_id).isdigit():
                    continue
                return str(channel_id)
        return None

    async def handle_update_button_click(self, context: MessageContext, target_version: Optional[str] = None) -> None:
        """Handle update button click for Discord (non-Slack)."""
        im_client = self.controller.get_im_client_for_context(context)
        message_id = context.message_id
        if not message_id:
            await im_client.send_message(context, "Update action unavailable for this message.")
            return
        if self._upgrade_lock.locked():
            await im_client.edit_message(
                context,
                context.message_id,
                text="Upgrade already in progress. Please wait.",
            )
            return

        if not target_version:
            version_info = await self._get_version_info_async()
            if version_info.get("error"):
                await im_client.edit_message(context, message_id, text="Update check failed. Please try again later.")
                return
            if not version_info.get("has_update"):
                await im_client.edit_message(context, message_id, text="Already up to date.")
                return
            target_version = version_info.get("latest")
            if not target_version:
                await im_client.edit_message(
                    context, message_id, text="Update information unavailable. Please try again later."
                )
                return

        await im_client.edit_message(context, message_id, text="Updating Vibe Remote...")
        result = await self._perform_update(target_version, channel_id=context.channel_id, message_id=message_id)
        if not result.get("ok"):
            await im_client.edit_message(context, message_id, text="Upgrade failed. Please check logs.")
        elif not result.get("restarting"):
            await im_client.edit_message(context, message_id, text="Upgrade complete. Please restart Vibe Remote.")

    async def _perform_update(
        self, target_version: str, channel_id: Optional[str] = None, message_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Perform the actual update and restart. Returns do_upgrade result dict."""
        # Prevent concurrent upgrades
        if self._upgrade_lock.locked():
            logger.warning("Upgrade already in progress, skipping")
            return {
                "ok": False,
                "message": "Upgrade already in progress",
                "output": None,
                "restarting": False,
            }

        async with self._upgrade_lock:
            logger.info(f"Starting auto-update to version {target_version}")

            # Run upgrade in thread to avoid blocking event loop
            from vibe.api import do_upgrade

            result = await asyncio.to_thread(do_upgrade, True)

            if result["ok"]:
                logger.info(f"Upgrade successful: {result['message']}")
                if result.get("restarting"):
                    # Write marker only if restart is scheduled
                    self._write_update_marker(target_version, channel_id=channel_id, message_id=message_id)
                else:
                    logger.warning("Upgrade completed without restart; manual restart required")
                return result
            else:
                logger.error(f"Upgrade failed: {result['message']}")
                if result.get("output"):
                    logger.error(f"Output: {result['output']}")
                self._remove_update_marker()
                return result

    def _write_update_marker(
        self, version: str, channel_id: Optional[str] = None, message_id: Optional[str] = None
    ) -> None:
        """Write a marker file to trigger post-update notification."""
        try:
            marker_path = paths.get_state_dir() / "pending_update_notification.json"
            marker_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": version,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            # Store message coordinates for updating the original message after restart
            if channel_id and message_id:
                data["channel_id"] = channel_id
                data["message_id"] = message_id

            # Atomic write
            with tempfile.NamedTemporaryFile(
                mode="w", dir=marker_path.parent, suffix=".tmp", delete=False, encoding="utf-8"
            ) as f:
                json.dump(data, f)
                temp_path = Path(f.name)
            temp_path.replace(marker_path)
        except Exception as e:
            logger.error(f"Failed to write update marker: {e}")

    def _remove_update_marker(self) -> None:
        """Remove the update marker file."""
        try:
            marker_path = paths.get_state_dir() / "pending_update_notification.json"
            marker_path.unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"Failed to remove update marker: {e}")

    async def check_and_send_post_update_notification(self) -> None:
        """Check for pending update notification and send it (called on startup)."""
        marker_path = paths.get_state_dir() / "pending_update_notification.json"
        if not marker_path.exists():
            return

        try:
            data = json.loads(marker_path.read_text(encoding="utf-8"))
            channel_id = data.get("channel_id")
            message_id = data.get("message_id")
            # Use the target version from marker (more reliable than __version__ in edge cases)
            target_version = data.get("version", "unknown")

            platform = getattr(self.controller.config, "platform", "slack")
            if channel_id:
                platform = _infer_channel_platform(channel_id)
            im_client = self._get_im_client_for_platform(platform)
            if platform == "discord":
                success_text = f"✅ Vibe Remote has been updated to `{target_version}`"
            else:
                success_text = f":white_check_mark: Vibe Remote has been updated to `{target_version}`"
            success_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":white_check_mark: *Vibe Remote Updated Successfully*\n\n"
                        f"Now running version `{target_version}`",
                    },
                }
            ]

            # If we have original message coordinates, update that message
            if channel_id and message_id and platform == "slack":
                await im_client.web_client.chat_update(
                    channel=channel_id,
                    ts=message_id,
                    text=success_text,
                    blocks=success_blocks,
                )
                logger.info("Updated original message with post-update notification")
            elif channel_id and message_id and platform == "discord":
                try:
                    from modules.im import MessageContext

                    context = MessageContext(user_id="system", channel_id=channel_id, platform="discord")
                    await im_client.edit_message(context, message_id, text=success_text)
                    logger.info("Updated Discord message with post-update notification")
                except Exception as e:
                    logger.error("Failed to edit Discord update message: %s", e)
            else:
                # Fallback: send a new message to admins, or workspace owner
                admin_ids = self._get_admin_user_ids()
                if admin_ids:
                    for uid in admin_ids:
                        try:
                            admin_client, raw_user_id, _ = self._get_im_client_for_user(uid)
                            await admin_client.send_dm(raw_user_id, success_text)
                            logger.info(f"Sent post-update notification to admin {uid}")
                        except Exception as e:
                            logger.error(f"Failed to send post-update notification to admin {uid}: {e}")
                elif platform == "slack":
                    # Legacy fallback: try workspace owner
                    owner_id = await self._get_workspace_owner_id()
                    if owner_id:
                        dm_channel = await self._open_dm_channel(owner_id)
                        if dm_channel:
                            await im_client.web_client.chat_postMessage(
                                channel=dm_channel,
                                text=success_text,
                                blocks=success_blocks,
                            )
                            logger.info(f"Sent post-update notification to {owner_id}")
        except Exception as e:
            logger.error(f"Failed to send post-update notification: {e}")
        finally:
            marker_path.unlink(missing_ok=True)


async def handle_update_button_click(controller: "Controller", payload: Dict[str, Any]) -> None:
    """Handle the 'Update Now' button click from Slack.

    This function should return quickly to avoid Slack ack timeout.
    The actual update is performed in a background task.
    """
    channel_id = payload.get("channel", {}).get("id")
    message_id = payload.get("message", {}).get("ts")
    im_client = (
        controller._get_im_client_for_platform("slack")
        if hasattr(controller, "_get_im_client_for_platform")
        else controller.im_client
    )

    # Check if upgrade is already in progress
    if hasattr(controller, "update_checker") and controller.update_checker._upgrade_lock.locked():
        try:
            await im_client.web_client.chat_update(
                channel=channel_id,
                ts=message_id,
                text="Upgrade already in progress",
                blocks=[
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": ":warning: An upgrade is already in progress. Please wait."},
                    }
                ],
            )
        except Exception as e:
            logger.error(f"Failed to update message: {e}")
        return

    # Update message immediately to acknowledge the click
    try:
        await im_client.web_client.chat_update(
            channel=channel_id,
            ts=message_id,
            text="Updating Vibe Remote...",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": ":hourglass_flowing_sand: *Updating Vibe Remote...*\n\nPlease wait, the service will restart shortly.",
                    },
                }
            ],
        )
    except Exception as e:
        logger.error(f"Failed to acknowledge button click: {e}")
        return

    # Schedule the actual update in a background task to avoid blocking
    asyncio.create_task(_do_update_from_button(controller, channel_id, message_id))


async def _do_update_from_button(controller: "Controller", channel_id: str, message_id: str) -> None:
    """Background task to perform update after button click."""
    try:
        if not hasattr(controller, "update_checker"):
            return

        update_checker = controller.update_checker
        im_client = (
            controller._get_im_client_for_platform("slack")
            if hasattr(controller, "_get_im_client_for_platform")
            else controller.im_client
        )

        # Check for updates
        version_info = await update_checker._get_version_info_async()

        if version_info.get("has_update"):
            # Perform the update
            result = await update_checker._perform_update(
                version_info["latest"], channel_id=channel_id, message_id=message_id
            )
            if not result.get("ok"):
                # Update failed, show error
                await im_client.web_client.chat_update(
                    channel=channel_id,
                    ts=message_id,
                    text="Upgrade failed",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": ":x: *Upgrade Failed*\n\nPlease check the logs for details.",
                            },
                        }
                    ],
                )
            elif not result.get("restarting"):
                # Upgrade succeeded but restart not scheduled
                await im_client.web_client.chat_update(
                    channel=channel_id,
                    ts=message_id,
                    text="Upgrade completed - restart required",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": ":white_check_mark: *Upgrade complete*\n\nPlease restart Vibe Remote to apply the update.",
                            },
                        }
                    ],
                )
        else:
            # No update available
            await im_client.web_client.chat_update(
                channel=channel_id,
                ts=message_id,
                text="Already up to date",
                blocks=[
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": ":white_check_mark: Already running the latest version."},
                    }
                ],
            )
    except Exception as e:
        logger.error(f"Failed to perform update from button click: {e}", exc_info=True)


from modules.im import MessageContext
