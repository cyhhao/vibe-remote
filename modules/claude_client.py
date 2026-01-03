import base64
import logging
import os
from typing import Optional, Callable, List, Tuple, Any
from claude_code_sdk import (
    ClaudeCodeOptions,
    SystemMessage,
    AssistantMessage,
    UserMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
)
from config import ClaudeConfig
from modules.im.formatters import BaseMarkdownFormatter, TelegramFormatter
from modules.im import ImageData


logger = logging.getLogger(__name__)


class ClaudeClient:
    def __init__(
        self, config: ClaudeConfig, formatter: Optional[BaseMarkdownFormatter] = None
    ):
        self.config = config
        self.formatter = (
            formatter or TelegramFormatter()
        )  # Default to Telegram for backward compatibility
        self.options = ClaudeCodeOptions(
            permission_mode=config.permission_mode,
            cwd=config.cwd,
            system_prompt=config.system_prompt,
        )

    def format_message(
        self, message, get_relative_path: Optional[Callable[[str], str]] = None
    ) -> str:
        """Format different types of messages according to specified rules"""
        try:
            if isinstance(message, SystemMessage):
                return self._format_system_message(message)
            elif isinstance(message, AssistantMessage):
                return self._format_assistant_message(message, get_relative_path)
            elif isinstance(message, UserMessage):
                return self._format_user_message(message, get_relative_path)
            elif isinstance(message, ResultMessage):
                return self._format_result_message(message)
            else:
                return self.formatter.format_warning(
                    f"Unknown message type: {type(message)}"
                )
        except Exception as e:
            logger.error(f"Error formatting message: {e}")
            return self.formatter.format_error(f"Error formatting message: {str(e)}")

    def _process_content_blocks(
        self, content_blocks, get_relative_path: Optional[Callable[[str], str]] = None
    ) -> list:
        """Process content blocks (TextBlock, ToolUseBlock) and return formatted parts"""
        formatted_parts = []

        for block in content_blocks:
            if isinstance(block, TextBlock):
                # Don't escape here - let the formatter handle it during final formatting
                # This avoids double escaping
                formatted_parts.append(block.text)
            elif isinstance(block, ToolUseBlock):
                tool_info = self._format_tool_use_block(block, get_relative_path)
                formatted_parts.append(tool_info)
            elif isinstance(block, ToolResultBlock):
                result_info = self._format_tool_result_block(block)
                formatted_parts.append(result_info)

        return formatted_parts

    def _get_relative_path(self, full_path: str) -> str:
        """Convert absolute path to relative path based on ClaudeCode cwd"""
        # Get ClaudeCode's current working directory
        cwd = self.options.cwd or os.getcwd()

        # Normalize paths for consistent comparison
        cwd = os.path.normpath(cwd)
        full_path = os.path.normpath(full_path)

        try:
            # If the path starts with cwd, make it relative
            if full_path.startswith(cwd + os.sep) or full_path == cwd:
                relative = os.path.relpath(full_path, cwd)
                # Use "./" prefix for current directory files
                if not relative.startswith(".") and relative != ".":
                    relative = "./" + relative
                return relative
            else:
                # If not under cwd, just return the path as is
                return full_path
        except:
            # Fallback to original path if any error
            return full_path

    def _format_tool_use_block(
        self,
        block: ToolUseBlock,
        get_relative_path: Optional[Callable[[str], str]] = None,
    ) -> str:
        """Format ToolUseBlock using formatter"""
        # Prefer caller-provided get_relative_path (per-session cwd), fallback to self
        rel = get_relative_path if get_relative_path else self._get_relative_path
        return self.formatter.format_tool_use(
            block.name, block.input, get_relative_path=rel
        )

    def _format_tool_result_block(self, block: ToolResultBlock) -> str:
        """Format ToolResultBlock using formatter"""
        return self.formatter.format_tool_result(block.is_error, block.content)

    def _format_system_message(self, message: SystemMessage) -> str:
        """Format SystemMessage using formatter"""
        cwd = message.data.get("cwd", "Unknown")
        session_id = message.data.get("session_id", None)
        return self.formatter.format_system_message(cwd, message.subtype, session_id)

    def _format_assistant_message(
        self,
        message: AssistantMessage,
        get_relative_path: Optional[Callable[[str], str]] = None,
    ) -> str:
        """Format AssistantMessage using formatter"""
        content_parts = self._process_content_blocks(message.content, get_relative_path)
        return self.formatter.format_assistant_message(content_parts)

    def _format_user_message(
        self,
        message: UserMessage,
        get_relative_path: Optional[Callable[[str], str]] = None,
    ) -> str:
        """Format UserMessage using formatter"""
        content_parts = self._process_content_blocks(message.content, get_relative_path)
        return self.formatter.format_user_message(content_parts)

    def _format_result_message(self, message: ResultMessage) -> str:
        """Format ResultMessage using formatter"""
        return self.formatter.format_result_message(
            message.subtype, message.duration_ms, message.result
        )

    def _is_skip_message(self, message) -> bool:
        """Check if the message should be skipped"""
        if isinstance(message, AssistantMessage):
            if not message.content:
                return True
        elif isinstance(message, UserMessage):
            if not message.content:
                return True
        return False

    def extract_images_from_message(self, message) -> List[ImageData]:
        """Extract images from a message's content blocks.

        Images are typically found in ToolResultBlock content as:
        [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "..."}}]

        Args:
            message: A Claude SDK message (AssistantMessage, UserMessage, etc.)

        Returns:
            List of ImageData objects
        """
        images = []

        # Get content blocks from the message
        content_blocks = getattr(message, "content", []) or []

        for block in content_blocks:
            if isinstance(block, ToolResultBlock):
                # ToolResultBlock.content can be str or list[dict]
                images.extend(self._extract_images_from_tool_result(block))

        return images

    def _extract_images_from_tool_result(self, block: ToolResultBlock) -> List[ImageData]:
        """Extract images from a ToolResultBlock.

        Args:
            block: A ToolResultBlock that may contain images

        Returns:
            List of ImageData objects
        """
        images = []
        content = block.content

        # If content is a list, look for image blocks
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "image":
                    image_data = self._parse_image_block(item)
                    if image_data:
                        images.append(image_data)

        return images

    def _parse_image_block(self, image_block: dict) -> Optional[ImageData]:
        """Parse an image block and return ImageData.

        Image block format:
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": "base64-encoded-data"
            }
        }

        Args:
            image_block: Dictionary containing image data

        Returns:
            ImageData object or None if parsing fails
        """
        try:
            source = image_block.get("source", {})

            if source.get("type") != "base64":
                logger.warning(f"Unsupported image source type: {source.get('type')}")
                return None

            media_type = source.get("media_type", "image/png")
            base64_data = source.get("data", "")

            if not base64_data:
                logger.warning("Empty image data")
                return None

            # Decode base64 data
            try:
                image_bytes = base64.b64decode(base64_data)
            except Exception as e:
                logger.error(f"Failed to decode base64 image data: {e}")
                return None

            # Generate filename based on media type
            extension = media_type.split("/")[-1] if "/" in media_type else "png"
            filename = f"screenshot.{extension}"

            return ImageData(
                data=image_bytes,
                media_type=media_type,
                filename=filename,
            )

        except Exception as e:
            logger.error(f"Error parsing image block: {e}")
            return None
