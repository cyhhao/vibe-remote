"""Periodic status updater for long-running agent tasks."""

import asyncio
import logging
import time
from typing import Optional, Callable, Awaitable

logger = logging.getLogger(__name__)


class StatusUpdater:
    """Updates a message periodically with current status information.

    Provides visual feedback during long-running agent operations by
    updating the acknowledgement message with elapsed time and current activity.
    """

    UPDATE_INTERVAL = 5  # seconds between updates

    def __init__(
        self,
        edit_message: Callable[[str, str, str], Awaitable[bool]],
        channel_id: str,
        message_id: str,
        thread_id: Optional[str] = None,
        agent_name: str = "Claude",
    ):
        """Initialize the status updater.

        Args:
            edit_message: Async function to edit messages (channel_id, thread_id, message_id, text) -> bool
            channel_id: Channel where the message lives
            message_id: ID of the message to update
            thread_id: Optional thread ID for Slack
            agent_name: Name of the agent for display
        """
        self._edit_message = edit_message
        self._channel_id = channel_id
        self._message_id = message_id
        self._thread_id = thread_id
        self._agent_name = agent_name
        self._started_at = time.monotonic()
        self._current_activity: str = "Processing your request"
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def set_activity(self, activity: str) -> None:
        """Update the current activity description."""
        self._current_activity = activity

    def _format_elapsed(self) -> str:
        """Format elapsed time as a human-readable string."""
        elapsed = int(time.monotonic() - self._started_at)
        if elapsed < 60:
            return f"{elapsed}s"
        minutes = elapsed // 60
        seconds = elapsed % 60
        return f"{minutes}m {seconds}s"

    def _get_spinner(self) -> str:
        """Get a spinner character based on elapsed time."""
        spinners = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        elapsed = int(time.monotonic() - self._started_at)
        return spinners[elapsed % len(spinners)]

    def _build_status_text(self) -> str:
        """Build the status message text."""
        spinner = self._get_spinner()
        elapsed = self._format_elapsed()
        return (
            f"{spinner} *{self._agent_name}* is working... ({elapsed})\n"
            f"_{self._current_activity}_"
        )

    async def _update_loop(self) -> None:
        """Background loop that updates the message periodically."""
        try:
            while self._running:
                await asyncio.sleep(self.UPDATE_INTERVAL)
                if not self._running:
                    break

                text = self._build_status_text()
                try:
                    await self._edit_message(
                        self._channel_id,
                        self._thread_id,
                        self._message_id,
                        text
                    )
                except Exception as e:
                    logger.debug(f"Failed to update status message: {e}")
                    # Don't stop the loop on transient errors
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Status updater loop error: {e}")

    def start(self) -> None:
        """Start the periodic update loop."""
        if self._task is not None:
            return
        self._running = True
        self._task = asyncio.create_task(self._update_loop())

    async def stop(self, update_final: bool = True) -> None:
        """Stop the periodic update loop.

        Args:
            update_final: If True, update the message to show completion status.
        """
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        # Update message to show completion
        if update_final:
            try:
                elapsed = self._format_elapsed()
                completion_text = f"✓ *{self._agent_name}* finished ({elapsed})"
                await self._edit_message(
                    self._channel_id,
                    self._thread_id,
                    self._message_id,
                    completion_text
                )
            except Exception as e:
                logger.debug(f"Failed to update completion status: {e}")
