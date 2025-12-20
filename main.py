#!/usr/bin/env python3
import os
import sys
import logging
import asyncio
from dotenv import load_dotenv
from rich.console import Console
from rich.logging import RichHandler
from config.settings import AppConfig
from core.controller import Controller

# Load environment variables from .env file
load_dotenv()

# Create console for rich output
console = Console()


def setup_logging(level: str = "INFO"):
    """Setup logging configuration with RichHandler for pretty console output"""
    # Ensure logs directory exists
    logs_dir = 'logs'
    try:
        os.makedirs(logs_dir, exist_ok=True)
    except Exception:
        # Fallback to current directory if logs dir cannot be created
        logs_dir = '.'

    # Configure logging with Rich handler for pretty console output
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(message)s",
        handlers=[
            RichHandler(console=console, rich_tracebacks=True),
            logging.FileHandler(f"{logs_dir}/vibe_remote.log"),
        ],
    )


def main():
    """Main entry point"""
    try:
        # Load configuration
        config = AppConfig.from_env()

        # Setup logging with Rich handler
        setup_logging(config.log_level)
        logger = logging.getLogger(__name__)

        logger.info("[bold green]Starting vibe-remote service...[/]")
        logger.info(f"Working directory: [cyan]{config.claude.cwd}[/]")

        # Create and run controller
        controller = Controller(config)
        controller.run()

    except Exception as e:
        logger.error(f"[bold red]Failed to start: {e}[/]")
        sys.exit(1)


if __name__ == "__main__":
    main()
