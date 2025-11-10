#!/usr/bin/env python3
import os
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


def main():
    """Main entry point"""
    try:
        # Load configuration
        config = AppConfig.from_env()
        
        # Setup logging
        setup_logging(config.log_level)
        logger = logging.getLogger(__name__)
        
        logger.info("Starting vibe-remote service...")
        logger.info(f"Working directory: {config.claude.cwd}")
        
        # Create and run controller
        controller = Controller(config)
        controller.run()
        
    except Exception as e:
        logging.error(f"Failed to start: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
