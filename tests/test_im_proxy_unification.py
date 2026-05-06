"""Unified tests for the IM proxy_url field and resolve_proxy() precedence.

Covers:
  - proxy_url is inherited by every IM config dataclass.
  - resolve_proxy() honors explicit config first, then falls back to the
    system SOCKS proxy, otherwise returns None.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.v2_config import (
    DiscordConfig,
    LarkConfig,
    SlackConfig,
    TelegramConfig,
    WeChatConfig,
)
from vibe import proxy as proxy_module
from vibe.proxy import resolve_proxy


CONFIG_FACTORIES = [
    pytest.param(lambda **kw: SlackConfig(bot_token="xoxb-test", **kw), id="slack"),
    pytest.param(lambda **kw: DiscordConfig(bot_token="0123456789abc", **kw), id="discord"),
    pytest.param(lambda **kw: TelegramConfig(bot_token="123456:abc", **kw), id="telegram"),
    pytest.param(lambda **kw: LarkConfig(app_id="cli_x", app_secret="s", **kw), id="lark"),
    pytest.param(lambda **kw: WeChatConfig(bot_token="wx-test", **kw), id="wechat"),
]


# ---------------------------------------------------------------------------
# proxy_url is inherited from BaseIMConfig by every IM config
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("factory", CONFIG_FACTORIES)
def test_proxy_url_default_none(factory) -> None:
    """Every IM config defaults proxy_url to None."""
    config = factory()
    assert hasattr(config, "proxy_url")
    assert config.proxy_url is None


@pytest.mark.parametrize(
    "factory",
    [
        pytest.param(lambda **kw: SlackConfig(bot_token="xoxb-test", **kw), id="slack"),
        pytest.param(lambda **kw: DiscordConfig(bot_token="0123456789abc", **kw), id="discord"),
        pytest.param(lambda **kw: TelegramConfig(bot_token="123456:abc", **kw), id="telegram"),
        pytest.param(lambda **kw: WeChatConfig(bot_token="wx-test", **kw), id="wechat"),
    ],
)
def test_proxy_url_accepts_socks5(factory) -> None:
    config = factory(proxy_url="socks5://user:pass@127.0.0.1:1080")
    assert config.proxy_url == "socks5://user:pass@127.0.0.1:1080"


def test_lark_config_accepts_proxy_url() -> None:
    """Lark/Feishu config still accepts proxy_url even though the SDK ignores it.

    The runtime warns at adapter init; the field itself must round-trip so the
    UI and persisted config keep working.
    """
    config = LarkConfig(app_id="cli_x", app_secret="s", proxy_url="socks5://127.0.0.1:1080")
    assert config.proxy_url == "socks5://127.0.0.1:1080"


@pytest.mark.parametrize("factory", CONFIG_FACTORIES)
def test_proxy_url_accepts_http(factory) -> None:
    """HTTP proxies are valid for adapters that honor the field."""
    config = factory(proxy_url="http://127.0.0.1:8080")
    assert config.proxy_url == "http://127.0.0.1:8080"


# ---------------------------------------------------------------------------
# resolve_proxy() precedence
# ---------------------------------------------------------------------------


def test_resolve_proxy_returns_explicit_config() -> None:
    """Explicit config_proxy wins over any system fallback."""
    with patch.object(proxy_module, "get_system_socks_proxy", return_value="socks5://system:1080"):
        assert resolve_proxy("socks5://explicit:1080") == "socks5://explicit:1080"


def test_resolve_proxy_strips_whitespace() -> None:
    """Surrounding whitespace is stripped from explicit config."""
    with patch.object(proxy_module, "get_system_socks_proxy", return_value=None):
        assert resolve_proxy("  socks5://explicit:1080  ") == "socks5://explicit:1080"


def test_resolve_proxy_falls_back_to_system_when_none() -> None:
    """No config_proxy → fall back to the system SOCKS proxy."""
    with patch.object(proxy_module, "get_system_socks_proxy", return_value="socks5://system:1080"):
        assert resolve_proxy(None) == "socks5://system:1080"


def test_resolve_proxy_falls_back_to_system_when_empty_string() -> None:
    """Empty config_proxy is treated as unset and falls back to the system."""
    with patch.object(proxy_module, "get_system_socks_proxy", return_value="socks5://system:1080"):
        assert resolve_proxy("") == "socks5://system:1080"


def test_resolve_proxy_falls_back_to_system_when_whitespace_only() -> None:
    """Whitespace-only config_proxy is treated as unset."""
    with patch.object(proxy_module, "get_system_socks_proxy", return_value="socks5://system:1080"):
        assert resolve_proxy("   ") == "socks5://system:1080"


def test_resolve_proxy_returns_none_when_neither_configured() -> None:
    """No explicit config and no system proxy → None."""
    with patch.object(proxy_module, "get_system_socks_proxy", return_value=None):
        assert resolve_proxy(None) is None
        assert resolve_proxy("") is None
        assert resolve_proxy("   ") is None
