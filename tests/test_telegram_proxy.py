from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.v2_config import TelegramConfig


def test_telegram_config_proxy_url_default_none() -> None:
    """Test that proxy_url defaults to None when not specified."""
    config = TelegramConfig(bot_token="123456:test-token")
    assert config.proxy_url is None


def test_telegram_config_proxy_url_socks5() -> None:
    """Test that proxy_url can be set to SOCKS5 URL."""
    config = TelegramConfig(bot_token="123456:test-token", proxy_url="socks5://user:pass@127.0.0.1:1080")
    assert config.proxy_url == "socks5://user:pass@127.0.0.1:1080"


def test_telegram_config_proxy_url_http() -> None:
    """Test that proxy_url can be set to HTTP URL."""
    config = TelegramConfig(bot_token="123456:test-token", proxy_url="http://127.0.0.1:8080")
    assert config.proxy_url == "http://127.0.0.1:8080"


def test_telegram_config_proxy_url_socks4() -> None:
    """Test that proxy_url can be set to SOCKS4 URL."""
    config = TelegramConfig(bot_token="123456:test-token", proxy_url="socks4://127.0.0.1:1080")
    assert config.proxy_url == "socks4://127.0.0.1:1080"


def test_telegram_config_proxy_url_without_auth() -> None:
    """Test that proxy_url works without authentication."""
    config = TelegramConfig(bot_token="123456:test-token", proxy_url="socks5://127.0.0.1:1080")
    assert config.proxy_url == "socks5://127.0.0.1:1080"