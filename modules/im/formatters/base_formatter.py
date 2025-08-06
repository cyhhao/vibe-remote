from abc import ABC, abstractmethod
from typing import Optional, List, Tuple, Any, Dict
import json


class BaseMarkdownFormatter(ABC):
    """Abstract base class for platform-specific markdown formatters"""
    
    # Common formatting methods that work across platforms
    def format_code_inline(self, text: str) -> str:
        """Format inline code - same for most platforms"""
        return f"`{text}`"
    
    def format_code_block(self, code: str, language: str = "") -> str:
        """Format code block - same for most platforms"""
        return f"```{language}\n{code}\n```"
    
    def format_emoji(self, emoji: str) -> str:
        """Format emoji - same for all platforms"""
        return emoji
    
    def format_quote(self, text: str) -> str:
        """Format quoted text - commonly using >"""
        lines = text.split('\n')
        return '\n'.join(f"> {line}" for line in lines)
    
    def format_list_item(self, text: str, level: int = 0) -> str:
        """Format list item with indentation"""
        indent = "  " * level
        return f"{indent}• {text}"
    
    def format_numbered_list_item(self, text: str, number: int, level: int = 0) -> str:
        """Format numbered list item"""
        indent = "  " * level
        return f"{indent}{number}. {text}"
    
    def format_horizontal_rule(self) -> str:
        """Format horizontal rule"""
        return "---"
    
    # Core text formatting method
    def format_text(self, text: str, safe: bool = False) -> str:
        """Format plain text with automatic escaping
        
        Args:
            text: The text to format
            safe: If True, text is already escaped/formatted and won't be escaped again
            
        Returns:
            Formatted text with special characters escaped
        """
        if safe:
            return text
        return self.escape_special_chars(text)
    
    def format_plain(self, text: str) -> str:
        """Alias for format_text - formats plain text with escaping"""
        return self.format_text(text)
    
    # Platform-specific abstract methods
    @abstractmethod
    def format_bold(self, text: str) -> str:
        """Format bold text - platform specific"""
        pass
    
    @abstractmethod
    def format_italic(self, text: str) -> str:
        """Format italic text - platform specific"""
        pass
    
    @abstractmethod
    def format_strikethrough(self, text: str) -> str:
        """Format strikethrough text - platform specific"""
        pass
    
    @abstractmethod
    def format_link(self, text: str, url: str) -> str:
        """Format hyperlink - platform specific"""
        pass
    
    @abstractmethod
    def escape_special_chars(self, text: str) -> str:
        """Escape platform-specific special characters"""
        pass
    
    # High-level message composition methods
    def format_message(self, *lines) -> str:
        """Compose a message from multiple lines
        
        Args:
            *lines: Variable number of lines to compose
            
        Returns:
            Formatted message with proper line breaks
        """
        return "\n".join(str(line) for line in lines if line)
    
    def format_bullet_list(self, items: List[str], escape: bool = True) -> List[str]:
        """Format a list of items as bullet points
        
        Args:
            items: List of items to format
            escape: Whether to escape special characters in items
            
        Returns:
            List of formatted bullet points
        """
        formatted = []
        for item in items:
            if escape:
                item = self.format_text(item)
            formatted.append(f"• {item}")
        return formatted
    
    def format_definition_item(self, label: str, description: str) -> str:
        """Format a single definition item with label and description
        
        Args:
            label: The label/key text
            description: The description text
            
        Returns:
            Formatted definition item
        """
        # Default implementation - subclasses can override for platform-specific needs
        return f"• {self.format_bold(label)} - {self.format_text(description)}"
    
    def format_definition_list(self, items: List[Tuple[str, str]], bold_key: bool = True) -> List[str]:
        """Format a list of key-value pairs
        
        Args:
            items: List of (key, value) tuples
            bold_key: Whether to make keys bold
            
        Returns:
            List of formatted definition items
        """
        formatted = []
        for key, value in items:
            if bold_key:
                key_part = self.format_bold(key)
            else:
                key_part = self.format_text(key)
            value_part = self.format_text(value)
            formatted.append(f"• {key_part} - {value_part}")
        return formatted
    
    def format_info_message(self, title: str, emoji: str = "", items: List[Tuple[str, str]] = None, footer: str = "") -> str:
        """Format a complete info message with title, items, and optional footer
        
        Args:
            title: Message title
            emoji: Optional emoji for title
            items: Optional list of (label, description) tuples
            footer: Optional footer text
            
        Returns:
            Formatted info message
        """
        lines = []
        
        # Add header
        if emoji:
            lines.append(f"{emoji} {self.format_bold(title)}")
        else:
            lines.append(self.format_bold(title))
        
        # Add blank line after header
        if items or footer:
            lines.append("")
        
        # Add items
        if items:
            for label, description in items:
                # Use a platform-specific separator method
                lines.append(self.format_definition_item(label, description))
        
        # Add footer
        if footer:
            if items:
                lines.append("")
            lines.append(self.format_text(footer))
        
        return self.format_message(*lines)
    
    # Convenience methods that combine formatting
    def format_tool_name(self, tool_name: str, emoji: str = "🔧") -> str:
        """Format tool name with emoji and styling"""
        escaped_name = self.escape_special_chars(tool_name)
        return f"{emoji} {self.format_bold('Tool')}: {self.format_code_inline(escaped_name)}"
    
    def format_file_path(self, path: str, emoji: str = "📁") -> str:
        """Format file path with emoji"""
        escaped_path = self.escape_special_chars(path)
        return f"{emoji} File: {self.format_code_inline(escaped_path)}"
    
    def format_command(self, command: str) -> str:
        """Format shell command"""
        # For multi-line or long commands, use code block
        if "\n" in command or len(command) > 80:
            return f"💻 Command:\n{self.format_code_block(command, 'bash')}"
        else:
            escaped_cmd = self.escape_special_chars(command)
            return f"💻 Command: {self.format_code_inline(escaped_cmd)}"
    
    def format_error(self, error_text: str) -> str:
        """Format error message"""
        return f"❌ {self.format_bold('Error')}: {self.escape_special_chars(error_text)}"
    
    def format_success(self, message: str) -> str:
        """Format success message"""
        return f"✅ {self.escape_special_chars(message)}"
    
    def format_warning(self, warning_text: str) -> str:
        """Format warning message"""
        return f"⚠️ {self.format_bold('Warning')}: {self.escape_special_chars(warning_text)}"
    
    def format_section_header(self, title: str, emoji: str = "") -> str:
        """Format section header"""
        if emoji:
            return f"{emoji} {self.format_bold(title)}"
        return self.format_bold(title)
    
    def format_key_value(self, key: str, value: str, inline: bool = True) -> str:
        """Format key-value pair"""
        escaped_key = self.escape_special_chars(key)
        escaped_value = self.escape_special_chars(value)
        
        if inline:
            return f"{self.format_bold(escaped_key)}: {escaped_value}"
        else:
            return f"{self.format_bold(escaped_key)}:\n{escaped_value}"
    
    def truncate_text(self, text: str, max_length: int = 50, suffix: str = "...") -> str:
        """Truncate text to specified length"""
        if len(text) <= max_length:
            return text
        return text[:max_length] + suffix
    
    # Claude message formatting methods
    def format_system_message(self, cwd: str, subtype: str, session_id: Optional[str] = None) -> str:
        """Format system message"""
        header = self.format_section_header(f"System {subtype}", "🔧")
        cwd_line = self.format_file_path(cwd, emoji="📁").replace("File:", "Working directory:")
        
        # Add session ID if available
        if session_id:
            session_line = f"🔗 Session ID: {self.format_code_inline(session_id)}"
            ready_line = f"✨ Ready to work!"
            return f"{header}\n{cwd_line}\n{session_line}\n{ready_line}"
        else:
            ready_line = f"✨ Ready to work!"
            return f"{header}\n{cwd_line}\n{ready_line}"
    
    def format_assistant_message(self, content_parts: List[str]) -> str:
        """Format assistant message"""
        header = self.format_section_header("Assistant", "🤖")
        # Escape content parts that are plain text
        escaped_parts = []
        for part in content_parts:
            # Only escape if it's plain text (not already formatted with tool info)
            if not part.startswith(("🔧", "💻", "🔍", "📖", "✏️", "📝", "📄", "📓", "🌐", "✅", "❌", "🤖", "📂", "🔎", "🚪")):
                escaped_parts.append(self.escape_special_chars(part))
            else:
                # Already formatted tool output, don't escape
                escaped_parts.append(part)
        parts = [header] + escaped_parts
        return "\n\n".join(parts)
    
    def format_user_message(self, content_parts: List[str]) -> str:
        """Format user/response message"""
        header = self.format_section_header("Response", "👤")
        # Escape content parts that are plain text
        escaped_parts = []
        for part in content_parts:
            # Only escape if it's plain text (not already formatted)
            if not part.startswith(("🔧", "💻", "🔍", "📖", "✏️", "📝", "📄", "📓", "🌐", "✅", "❌", "🤖", "📂", "🔎", "🚪")):
                escaped_parts.append(self.escape_special_chars(part))
            else:
                # Already formatted output, don't escape
                escaped_parts.append(part)
        parts = [header] + escaped_parts
        return "\n\n".join(parts)
    
    def format_result_message(self, subtype: str, duration_ms: int, result: Optional[str] = None) -> str:
        """Format result message"""
        # Calculate duration
        total_seconds = duration_ms / 1000
        minutes = int(total_seconds // 60)
        seconds = int(total_seconds % 60)
        
        if minutes > 0:
            duration_str = f"{minutes}m {seconds}s"
        else:
            duration_str = f"{seconds}s"
        
        # Format result - don't include subtype in parentheses to avoid escaping issues
        header = self.format_section_header("Result", "📊")
        if subtype:
            header += f" {self.format_italic(subtype)}"
        duration_line = self.format_key_value("⏱️ Duration", duration_str)
        
        result_text = f"{header}\n{duration_line}"
        
        if result:
            result_text += f"\n\n{result}"
        
        return result_text
    
    def format_tool_result(self, is_error: bool, content: Optional[str] = None) -> str:
        """Format tool result block"""
        emoji = "❌" if is_error else "✅"
        result_info = f"{emoji} {self.format_bold('Tool Result')}"
        
        if content:
            content_str = str(content)
            if len(content_str) > 500:
                content_str = content_str[:500] + "..."
            result_info += f"\n{self.format_code_block(content_str)}"
        
        return result_info
    
    def format_todo_item(self, status: str, priority: str, content: str, completed: bool = False) -> str:
        """Format a todo item with status and priority"""
        status_emoji = {
            "pending": "⏳",
            "in_progress": "🔄", 
            "completed": "✅"
        }.get(status, "⏳")
        
        priority_emoji = {
            "high": "🔴",
            "medium": "🟡",
            "low": "🟢"
        }.get(priority, "🟡")
        
        # Truncate long content
        if len(content) > 50:
            content = content[:50] + "..."
        
        # Apply strikethrough for completed items
        if completed:
            formatted_content = self.format_strikethrough(content)
        else:
            formatted_content = self.escape_special_chars(content)
        
        return f"• {status_emoji} {priority_emoji} {formatted_content}"
    
    def format_tool_use(self, tool_name: str, tool_input: Dict[str, Any], 
                       get_relative_path: Optional[callable] = None) -> str:
        """Format tool use block with inputs"""
        # Determine tool emoji and category
        if tool_name.startswith("mcp__"):
            tool_category = tool_name.split("__")[1] if "__" in tool_name else "mcp"
            emoji_map = {
                "devmcp": "🔧",
                "db": "🗃️",
                "log": "📊",
                "ops": "⚡",
                "human": "👤",
            }
            emoji = emoji_map.get(tool_category, "🔧")
            tool_info = f"{emoji} {self.format_bold('MCP Tool')}: {self.format_code_inline(tool_name)}"
        else:
            tool_emoji_map = {
                "Task": "🤖",
                "Bash": "💻",
                "Glob": "🔍",
                "Grep": "🔎",
                "LS": "📂",
                "Read": "📖",
                "Edit": "✏️",
                "MultiEdit": "📝",
                "Write": "📄",
                "NotebookRead": "📓",
                "NotebookEdit": "📓",
                "WebFetch": "🌐",
                "WebSearch": "🔍",
                "TodoWrite": "✅",
                "ExitPlanMode": "🚪",
            }
            emoji = tool_emoji_map.get(tool_name, "🔧")
            tool_info = f"{emoji} {self.format_bold('Tool')}: {self.format_code_inline(tool_name)}"
        
        # Format tool inputs
        input_info = []
        
        # File operations
        if "file_path" in tool_input and tool_input["file_path"]:
            path = tool_input["file_path"]
            if get_relative_path:
                path = get_relative_path(path)
            input_info.append(self.format_file_path(path))
        
        # Path operations
        if "path" in tool_input and tool_input["path"]:
            path = tool_input["path"]
            if get_relative_path:
                path = get_relative_path(path)
            input_info.append(self.format_file_path(path, emoji="📂"))
        
        # Command operations
        if "command" in tool_input and tool_input["command"]:
            input_info.append(self.format_command(tool_input["command"]))
        
        # Description
        if "description" in tool_input and tool_input["description"]:
            input_info.append(f"📝 Description: {self.format_code_inline(tool_input['description'])}")
        
        # Pattern/Query
        if "pattern" in tool_input and tool_input["pattern"]:
            input_info.append(f"🔍 Pattern: {self.format_code_inline(tool_input['pattern'])}")
        
        if "query" in tool_input and tool_input["query"]:
            query_str = str(tool_input["query"])
            truncated = self.truncate_text(query_str, 50)
            input_info.append(f"🔍 Query: {self.format_code_inline(truncated)}")
        
        # URL
        if "url" in tool_input and tool_input["url"]:
            input_info.append(f"🌐 URL: {self.format_code_inline(str(tool_input['url']))}")
        
        # Prompt
        if "prompt" in tool_input and tool_input["prompt"]:
            prompt_str = str(tool_input["prompt"])
            truncated = self.truncate_text(prompt_str, 100)
            input_info.append(f"📝 Prompt: {self.escape_special_chars(truncated)}")
        
        # Edit operations
        if "old_string" in tool_input and tool_input["old_string"]:
            old_str = self.truncate_text(str(tool_input["old_string"]), 50)
            input_info.append(f"🔍 Old: {self.format_code_inline(old_str)}")
        
        if "new_string" in tool_input and tool_input["new_string"]:
            new_str = self.truncate_text(str(tool_input["new_string"]), 50)
            input_info.append(f"✏️ New: {self.format_code_inline(new_str)}")
        
        # MultiEdit
        if "edits" in tool_input and tool_input["edits"]:
            edits_count = len(tool_input["edits"])
            input_info.append(f"📝 Edits: {edits_count} changes")
        
        # Other common parameters
        if "limit" in tool_input and tool_input["limit"]:
            input_info.append(f"🔢 Limit: {tool_input['limit']}")
        
        if "offset" in tool_input and tool_input["offset"]:
            input_info.append(f"📍 Offset: {tool_input['offset']}")
        
        # Task tool
        if "subagent_type" in tool_input and tool_input["subagent_type"]:
            input_info.append(f"🤖 Agent: {self.format_code_inline(str(tool_input['subagent_type']))}")
        
        if "plan" in tool_input and tool_input["plan"]:
            plan_str = self.truncate_text(str(tool_input["plan"]), 100)
            input_info.append(f"📋 Plan: {self.escape_special_chars(plan_str)}")
        
        # Notebook operations
        if "cell_id" in tool_input and tool_input["cell_id"]:
            input_info.append(f"📊 Cell ID: {self.format_code_inline(str(tool_input['cell_id']))}")
        
        if "cell_type" in tool_input and tool_input["cell_type"]:
            input_info.append(f"📝 Cell Type: {self.format_code_inline(str(tool_input['cell_type']))}")
        
        # WebSearch
        if "allowed_domains" in tool_input and tool_input["allowed_domains"]:
            count = len(tool_input["allowed_domains"])
            input_info.append(f"✅ Allowed domains: {count}")
        
        if "blocked_domains" in tool_input and tool_input["blocked_domains"]:
            count = len(tool_input["blocked_domains"])
            input_info.append(f"🚫 Blocked domains: {count}")
        
        # Grep specific
        if "glob" in tool_input and tool_input["glob"]:
            input_info.append(f"🎯 Glob: {self.format_code_inline(str(tool_input['glob']))}")
        
        if "type" in tool_input and tool_input["type"]:
            input_info.append(f"📄 Type: {self.format_code_inline(str(tool_input['type']))}")
        
        if "output_mode" in tool_input and tool_input["output_mode"]:
            input_info.append(f"📊 Output mode: {self.format_code_inline(str(tool_input['output_mode']))}")
        
        # Combine tool info with inputs
        if input_info:
            tool_info += "\n" + "\n".join(input_info)
        
        # Handle special tool content formatting
        if tool_name == "TodoWrite" and "todos" in tool_input:
            todos = tool_input["todos"]
            tool_info += f"\n📋 {len(todos)} todo items:"
            for todo in todos:
                status = todo.get("status", "pending")
                priority = todo.get("priority", "medium")
                content = todo.get("content", "No content")
                completed = status == "completed"
                todo_line = self.format_todo_item(status, priority, content, completed)
                tool_info += f"\n{todo_line}"
        
        elif tool_name in ["Write", "Edit", "MultiEdit"] and "content" in tool_input:
            content = str(tool_input["content"])
            if len(content) > 300:
                content = content[:300] + "..."
            tool_info += f"\n{self.format_code_block(content)}"
        
        elif self._should_show_json(tool_name, tool_input):
            try:
                input_json = json.dumps(tool_input, indent=2, ensure_ascii=False)
                tool_info += f"\n{self.format_code_block(input_json, 'json')}"
            except:
                tool_info += f"\n{self.format_code_block(str(tool_input))}"
        
        return tool_info
    
    def _should_show_json(self, tool_name: str, tool_input: Dict[str, Any]) -> bool:
        """Determine if JSON should be shown for tool input"""
        no_json_tools = [
            "Bash", "Read", "Write", "Edit", "MultiEdit",
            "LS", "Glob", "Grep", "WebFetch", "WebSearch", "TodoWrite"
        ]
        return tool_name not in no_json_tools and tool_input and len(str(tool_input)) < 200