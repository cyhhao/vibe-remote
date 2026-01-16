#!/usr/bin/env python3
import os
import signal
import sys
import logging
import asyncio
from dotenv import load_dotenv
from config.settings import AppConfig
from core.controller import Controller

# Load environment variables from .env file
load_dotenv()


def setup_logging(level: str = "INFO"):
    """Setup logging configuration with file location and line numbers"""
    # Create a custom formatter with file location
    log_format = '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(funcName)s() - %(message)s'
    
    # For development, you can use this more detailed format:
    # log_format = '%(asctime)s - %(name)s - %(levelname)s - [%(pathname)s:%(lineno)d] - %(funcName)s() - %(message)s'
    
    # Ensure logs directory exists
    logs_dir = 'logs'
    try:
        os.makedirs(logs_dir, exist_ok=True)
    except Exception:
        # Fallback to current directory if logs dir cannot be created
        logs_dir = '.'

    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format=log_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(f"{logs_dir}/vibe_remote.log"),
        ],
    )


def apply_claude_sdk_patches():
    """Apply runtime patches for third-party SDK limits."""
    logger = logging.getLogger(__name__)
    try:
        from claude_code_sdk._internal.transport import subprocess_cli
    except Exception as exc:
        logger.warning(f"Claude SDK patch skipped: {exc}")
        return

    buffer_size = 16 * 1024 * 1024
    previous = getattr(subprocess_cli, "_MAX_BUFFER_SIZE", None)
    subprocess_cli._MAX_BUFFER_SIZE = buffer_size
    if previous != buffer_size:
        logger.info(
            "Patched claude_code_sdk _MAX_BUFFER_SIZE from %s to %s bytes",
            previous,
            buffer_size,
        )


def main():
    """Main entry point"""
    try:
        # Load configuration
        config = AppConfig.from_env()
        
        # Setup logging
        setup_logging(config.log_level)
        logger = logging.getLogger(__name__)

        apply_claude_sdk_patches()
        
        logger.info("Starting vibe-remote service...")
        logger.info(f"Working directory: {config.claude.cwd}")
        
        # Create and run controller
        controller = Controller(config)

        shutdown_initiated = False

        def _handle_shutdown(signum, frame):
            nonlocal shutdown_initiated
            if shutdown_initiated:
                return
            shutdown_initiated = True
            try:
                logger.info(f"Received signal {signum}, shutting down...")
            except Exception:
                pass
            try:
                controller.cleanup_sync()
            except Exception as cleanup_err:
                logger.error(f"Cleanup failed: {cleanup_err}")
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, _handle_shutdown)
        signal.signal(signal.SIGINT, _handle_shutdown)

        controller.run()
        
    except Exception as e:
        logging.error(f"Failed to start: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
