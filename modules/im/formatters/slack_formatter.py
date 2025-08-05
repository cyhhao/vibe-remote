from .base_formatter import BaseMarkdownFormatter


class SlackFormatter(BaseMarkdownFormatter):
    """Slack mrkdwn formatter
    
    Slack uses its own markup language called mrkdwn which is similar to Markdown
    but with some differences.
    Reference: https://api.slack.com/reference/surfaces/formatting
    """
    
    def format_bold(self, text: str) -> str:
        """Format bold text using single asterisks"""
        # In Slack, single asterisks make text bold
        return f"*{text}*"
    
    def format_italic(self, text: str) -> str:
        """Format italic text using underscores"""
        return f"_{text}_"
    
    def format_strikethrough(self, text: str) -> str:
        """Format strikethrough text using tildes"""
        return f"~{text}~"
    
    def format_link(self, text: str, url: str) -> str:
        """Format hyperlink in Slack style"""
        # Slack uses a different link format: <url|text>
        return f"<{url}|{text}>"
    
    def escape_special_chars(self, text: str) -> str:
        """Escape Slack mrkdwn special characters
        
        Slack requires escaping these characters: &, <, >
        """
        text = text.replace("&", "&amp;")
        text = text.replace("<", "&lt;")
        text = text.replace(">", "&gt;")
        return text
    
    def format_code_inline(self, text: str) -> str:
        """Format inline code - same as base but with proper escaping"""
        # In Slack, backticks work the same way
        # But we need to ensure special chars are escaped outside of code
        return f"`{text}`"
    
    def format_code_block(self, code: str, language: str = "") -> str:
        """Format code block - Slack style"""
        # Slack also supports triple backticks for code blocks
        # Language hint is not displayed but can be included
        return f"```\n{code}\n```"
    
    def format_quote(self, text: str) -> str:
        """Format quoted text - Slack style"""
        # Slack uses > for quotes, same as standard markdown
        lines = text.split('\n')
        return '\n'.join(f">{line}" for line in lines)
    
    def format_list_item(self, text: str, level: int = 0) -> str:
        """Format list item - Slack style"""
        # Slack doesn't support nested lists well, so we simplify
        if level == 0:
            return f"• {text}"
        else:
            # Use spaces for indentation
            indent = "  " * level
            return f"{indent}◦ {text}"
    
    def format_numbered_list_item(self, text: str, number: int, level: int = 0) -> str:
        """Format numbered list item - Slack style"""
        indent = "  " * level
        return f"{indent}{number}. {text}"
    
    # Slack-specific formatting methods
    def format_user_mention(self, user_id: str) -> str:
        """Format user mention in Slack style"""
        return f"<@{user_id}>"
    
    def format_channel_mention(self, channel_id: str) -> str:
        """Format channel mention in Slack style"""
        return f"<#{channel_id}>"
    
    def format_emoji(self, emoji: str, name: str = None) -> str:
        """Format emoji - Slack supports both Unicode and :name: format"""
        if name:
            return f":{name}:"
        return emoji
    
    # Override convenience methods for Slack-specific behavior
    def format_section_header(self, title: str, emoji: str = "") -> str:
        """Format section header - Slack doesn't have headers, so we use bold"""
        if emoji:
            return f"{emoji} {self.format_bold(title)}"
        return self.format_bold(title)
    
    def format_horizontal_rule(self) -> str:
        """Format horizontal rule - Slack doesn't support this well"""
        # Use a series of dashes as a visual separator
        return "─" * 40
    
    def format_tool_name(self, tool_name: str, emoji: str = "🔧") -> str:
        """Format tool name with emoji and styling"""
        # Don't escape tool name in code blocks
        return f"{emoji} {self.format_bold('Tool')}: {self.format_code_inline(tool_name)}"
    
    def format_file_path(self, path: str, emoji: str = "📁") -> str:
        """Format file path with emoji"""
        # Don't escape path in code blocks
        return f"{emoji} File: {self.format_code_inline(path)}"
    
    def format_command(self, command: str) -> str:
        """Format shell command"""
        # For multi-line or long commands, use code block
        if "\n" in command or len(command) > 80:
            return f"💻 Command:\n{self.format_code_block(command)}"
        else:
            return f"💻 Command: {self.format_code_inline(command)}"