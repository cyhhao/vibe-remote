#!/usr/bin/env python3
import os
import signal
import sys
import logging
import asyncio
from config.paths import ensure_data_dirs, get_logs_dir
from config.v2_config import V2Config
from core.controller import Controller
from vibe.sentry_integration import init_sentry


def _build_logging_handlers(logs_dir: str) -> list[logging.Handler]:
    handlers: list[logging.Handler] = [logging.FileHandler(f"{logs_dir}/vibe_remote.log")]
    if os.environ.get("VIBE_DISABLE_STDOUT_LOGGING", "").lower() not in {"1", "true", "yes"}:
        handlers.insert(0, logging.StreamHandler(sys.stdout))
    return handlers


def setup_logging(level: str = "INFO"):
    """Setup logging configuration with file location and line numbers"""
    # Create a custom formatter with file location
    log_format = '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(funcName)s() - %(message)s'
    
    # For development, you can use this more detailed format:
    # log_format = '%(asctime)s - %(name)s - %(levelname)s - [%(pathname)s:%(lineno)d] - %(funcName)s() - %(message)s'
    
    ensure_data_dirs()
    logs_dir = str(get_logs_dir())

    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format=log_format,
        handlers=_build_logging_handlers(logs_dir),
    )


def apply_claude_sdk_patches():
    """Apply runtime patches for third-party SDK limits."""
    logger = logging.getLogger(__name__)
    try:
        from modules.claude_sdk_compat import CLAUDE_SDK_MAX_BUFFER_SIZE
        from claude_agent_sdk._internal.transport import subprocess_cli
    except Exception as exc:
        logger.warning(f"Claude SDK patch skipped: {exc}")
        return

    patched = []
    for attr in ("_DEFAULT_MAX_BUFFER_SIZE", "_MAX_BUFFER_SIZE"):
        if not hasattr(subprocess_cli, attr):
            continue
        previous = getattr(subprocess_cli, attr)
        setattr(subprocess_cli, attr, CLAUDE_SDK_MAX_BUFFER_SIZE)
        if previous != CLAUDE_SDK_MAX_BUFFER_SIZE:
            patched.append(f"{attr} from {previous} to {CLAUDE_SDK_MAX_BUFFER_SIZE} bytes")

    if patched:
        logger.info(
            "Patched claude_agent_sdk buffer limits: %s",
            ", ".join(patched),
        )


def main():
    """Main entry point"""
    try:
        # Load configuration
        config = V2Config.load()

        # Setup logging
        setup_logging(config.runtime.log_level)
        logger = logging.getLogger(__name__)

        apply_claude_sdk_patches()
        init_sentry(config, component="service")
        
        logger.info("Starting vibe-remote service...")
        logger.info(f"Working directory: {config.runtime.default_cwd}")
        
        # Create and run controller
        from config.v2_compat import to_app_config

        controller = Controller(to_app_config(config))

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
