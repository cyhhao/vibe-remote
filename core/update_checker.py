"""
Automatic update checker and installer.

This module provides:
1. Periodic checking for new versions on PyPI
2. Slack notifications to workspace owner when updates are available
3. Automatic update installation when the system is idle
"""

import asyncio
import json
import logging
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

from config import paths
from config.v2_config import UpdateConfig

if TYPE_CHECKING:
    from core.controller import Controller

logger = logging.getLogger(__name__)

# Action ID for the update button in Slack
UPDATE_BUTTON_ACTION_ID = "vibe_update_now"


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
        path = self._get_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "notified_version": self.notified_version,
            "notified_at": self.notified_at,
            "last_check_at": self.last_check_at,
            "last_activity_at": self.last_activity_at,
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

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

    def start(self) -> None:
        """Start the periodic update checker."""
        if self._running:
            return
        if self.config.check_interval_minutes <= 0:
            logger.info("Update checker disabled (check_interval_minutes=0)")
            return
        
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
        self.state.save()

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
            
            # Wait for next check interval
            await asyncio.sleep(self.config.check_interval_minutes * 60)

    async def _do_check(self) -> None:
        """Perform a single update check."""
        version_info = self._get_version_info()
        
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
        
        # Notification flow (independent)
        if self.config.notify_slack and self.state.notified_version != latest:
            await self._send_slack_notification(current, latest)
            self.state.notified_version = latest
            self.state.notified_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self.state.save()
        
        # Auto-update flow (independent)
        if self.config.auto_update and self._is_idle():
            logger.info("System is idle, performing auto-update...")
            await self._perform_update(latest)

    def _get_version_info(self) -> Dict[str, Any]:
        """Get current version and check for updates from PyPI."""
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
                    try:
                        current_parts = [int(x) for x in current.split(".")[:3] if x.isdigit()]
                        latest_parts = [int(x) for x in latest.split(".")[:3] if x.isdigit()]
                        result["has_update"] = latest_parts > current_parts
                    except (ValueError, AttributeError):
                        result["has_update"] = latest != current
        except Exception as e:
            result["error"] = str(e)
        
        return result

    def _is_idle(self) -> bool:
        """Check if the system is idle (no active sessions and no recent activity)."""
        # Check for active agent sessions
        if self._has_active_sessions():
            logger.debug("Not idle: has active sessions")
            return False
        
        # Check for recent activity
        if self.state.last_activity_at:
            idle_seconds = time.time() - self.state.last_activity_at
            idle_minutes = idle_seconds / 60
            if idle_minutes < self.config.idle_minutes:
                logger.debug(f"Not idle: last activity {idle_minutes:.1f} minutes ago")
                return False
        
        return True

    def _has_active_sessions(self) -> bool:
        """Check if any agent has active sessions."""
        try:
            # Check OpenCode active polls
            if hasattr(self.controller, 'settings_manager'):
                active_polls = self.controller.settings_manager.get_all_active_polls()
                if active_polls:
                    return True
            
            # Check Claude sessions
            if hasattr(self.controller, 'claude_sessions') and self.controller.claude_sessions:
                return True
            
            # Check Codex active processes
            if hasattr(self.controller, 'agent_service'):
                codex = self.controller.agent_service.agents.get("codex")
                if codex and hasattr(codex, 'active_processes') and codex.active_processes:
                    return True
        except Exception as e:
            logger.warning(f"Error checking active sessions: {e}")
        
        return False

    async def _get_workspace_owner_id(self) -> Optional[str]:
        """Get the Slack workspace primary owner's user ID."""
        try:
            im_client = self.controller.im_client
            if not im_client or not hasattr(im_client, 'web_client'):
                return None
            
            response = await im_client.web_client.users_list()
            if not response.get("ok"):
                return None
            
            for member in response.get("members", []):
                if member.get("is_primary_owner"):
                    return member.get("id")
            
            # Fallback to any owner if no primary owner found
            for member in response.get("members", []):
                if member.get("is_owner"):
                    return member.get("id")
        except Exception as e:
            logger.warning(f"Failed to get workspace owner: {e}")
        
        return None

    async def _send_slack_notification(self, current: str, latest: str) -> None:
        """Send a Slack notification about the available update."""
        owner_id = await self._get_workspace_owner_id()
        if not owner_id:
            logger.warning("Cannot send update notification: no workspace owner found")
            return
        
        try:
            im_client = self.controller.im_client
            blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":rocket: *Vibe Remote Update Available*\n\n"
                                f"A new version is available: `{current}` → `{latest}`"
                    }
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "Update Now",
                                "emoji": True
                            },
                            "style": "primary",
                            "action_id": UPDATE_BUTTON_ACTION_ID,
                            "value": latest
                        }
                    ]
                }
            ]
            
            await im_client.web_client.chat_postMessage(
                channel=owner_id,
                text=f"Vibe Remote update available: {current} → {latest}",
                blocks=blocks
            )
            logger.info(f"Sent update notification to workspace owner {owner_id}")
        except Exception as e:
            logger.error(f"Failed to send update notification: {e}")

    async def _perform_update(self, target_version: str) -> None:
        """Perform the actual update and restart."""
        logger.info(f"Starting auto-update to version {target_version}")
        
        # Write a marker file so we can send confirmation after restart
        self._write_update_marker(target_version)
        
        # Perform the upgrade
        exe_path = sys.executable
        is_uv_tool = ".local/share/uv/tools/" in exe_path or "/uv/tools/" in exe_path
        uv_path = shutil.which("uv")
        
        if is_uv_tool and uv_path:
            cmd = [uv_path, "tool", "upgrade", "vibe-remote"]
        else:
            cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "vibe-remote"]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode == 0:
                logger.info("Upgrade successful, restarting...")
                # Schedule restart
                vibe_path = shutil.which("vibe")
                if vibe_path:
                    subprocess.Popen(
                        f"sleep 2 && {vibe_path}",
                        shell=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
            else:
                logger.error(f"Upgrade failed: {result.stderr}")
                self._remove_update_marker()
        except Exception as e:
            logger.error(f"Upgrade failed: {e}")
            self._remove_update_marker()

    def _write_update_marker(self, version: str) -> None:
        """Write a marker file to trigger post-update notification."""
        marker_path = paths.get_state_dir() / "pending_update_notification.json"
        marker_path.write_text(json.dumps({
            "version": version,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }), encoding="utf-8")

    def _remove_update_marker(self) -> None:
        """Remove the update marker file."""
        marker_path = paths.get_state_dir() / "pending_update_notification.json"
        marker_path.unlink(missing_ok=True)

    async def check_and_send_post_update_notification(self) -> None:
        """Check for pending update notification and send it (called on startup)."""
        marker_path = paths.get_state_dir() / "pending_update_notification.json"
        if not marker_path.exists():
            return
        
        try:
            data = json.loads(marker_path.read_text(encoding="utf-8"))
            version = data.get("version")
            
            owner_id = await self._get_workspace_owner_id()
            if owner_id:
                from vibe import __version__
                im_client = self.controller.im_client
                await im_client.web_client.chat_postMessage(
                    channel=owner_id,
                    text=f":white_check_mark: Vibe Remote has been updated to `{__version__}`"
                )
                logger.info(f"Sent post-update notification to {owner_id}")
        except Exception as e:
            logger.error(f"Failed to send post-update notification: {e}")
        finally:
            marker_path.unlink(missing_ok=True)


async def handle_update_button_click(controller: "Controller", payload: Dict[str, Any]) -> None:
    """Handle the 'Update Now' button click from Slack."""
    try:
        user_id = payload.get("user", {}).get("id")
        channel_id = payload.get("channel", {}).get("id")
        message_ts = payload.get("message", {}).get("ts")
        
        im_client = controller.im_client
        
        # Acknowledge the button click with a loading message
        await im_client.web_client.chat_update(
            channel=channel_id,
            ts=message_ts,
            text="Updating Vibe Remote...",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": ":hourglass_flowing_sand: *Updating Vibe Remote...*\n\nPlease wait, the service will restart shortly."
                    }
                }
            ]
        )
        
        # Perform the update
        if hasattr(controller, 'update_checker'):
            version_info = controller.update_checker._get_version_info()
            if version_info.get("has_update"):
                await controller.update_checker._perform_update(version_info["latest"])
            else:
                await im_client.web_client.chat_update(
                    channel=channel_id,
                    ts=message_ts,
                    text="Already up to date",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": ":white_check_mark: Already running the latest version."
                            }
                        }
                    ]
                )
    except Exception as e:
        logger.error(f"Failed to handle update button click: {e}", exc_info=True)
