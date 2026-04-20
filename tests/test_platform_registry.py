from __future__ import annotations

from types import SimpleNamespace

import config.platform_registry as platform_registry
from config.platform_registry import PlatformCapabilities, PlatformDescriptor, get_platform_descriptor
from config.v2_config import PlatformsConfig, V2Config
from modules.im.factory import IMFactory


def test_platform_catalog_exposes_capability_flags() -> None:
    catalog = {item["id"]: item for item in platform_registry.platform_catalog_payload()}

    assert catalog["slack"]["capabilities"]["supports_threads"] is True
    assert catalog["telegram"]["capabilities"]["supports_threads"] is False
    assert catalog["wechat"]["capabilities"]["supports_buttons"] is False
    assert catalog["wechat"]["capabilities"]["supports_channels"] is False
    assert catalog["slack"]["capabilities"]["supports_typing_indicator"] is True
    assert catalog["slack"]["capabilities"]["typing_indicator_best_effort"] is True
    assert catalog["telegram"]["capabilities"]["supports_typing_indicator"] is True
    assert catalog["telegram"]["capabilities"]["typing_indicator_requires_clear"] is False
    assert catalog["wechat"]["capabilities"]["supports_typing_indicator"] is True
    assert catalog["wechat"]["capabilities"]["typing_indicator_requires_clear"] is True
    assert catalog["wechat"]["capabilities"]["force_preferred_processing_indicator"] is True
    assert catalog["lark"]["capabilities"]["supports_typing_indicator"] is False
    assert catalog["lark"]["capabilities"]["preferred_processing_indicator"] == "reaction"
    assert catalog["telegram"]["capabilities"]["supports_message_indicator_delete"] is True


def test_credential_readiness_comes_from_platform_descriptor() -> None:
    config = SimpleNamespace(lark=SimpleNamespace(app_id="cli_a", app_secret="secret"))

    assert get_platform_descriptor("lark").has_credentials(config) is True

    config.lark.app_secret = ""
    assert get_platform_descriptor("lark").has_credentials(config) is False


def test_descriptors_resolve_config_classes() -> None:
    config = get_platform_descriptor("telegram").create_config({"bot_token": "123456:test-token"})

    assert config.bot_token == "123456:test-token"


def test_registry_addition_drives_platform_validation_and_readiness(monkeypatch) -> None:
    descriptor = PlatformDescriptor(
        id="mockchat",
        config_key="mockchat",
        config_module="config.v2_config",
        config_class="SlackConfig",
        client_module="unused",
        client_class="Unused",
        formatter_module="unused",
        formatter_class="Unused",
        credential_fields=("token",),
        capabilities=PlatformCapabilities(
            supports_channels=True,
            supports_threads=False,
            supports_buttons=False,
            supports_quick_replies=False,
            supports_message_editing=False,
        ),
    )
    monkeypatch.setitem(platform_registry.PLATFORM_REGISTRY, descriptor.id, descriptor)

    platforms = PlatformsConfig(enabled=["mockchat"], primary="mockchat")
    platforms.validate()

    fake_config = SimpleNamespace(
        mode="self_host",
        platforms=platforms,
        mockchat=SimpleNamespace(token="configured"),
    )
    fake_config.enabled_platforms = V2Config.enabled_platforms.__get__(fake_config)
    fake_config.platform_has_credentials = V2Config.platform_has_credentials.__get__(fake_config)
    fake_config.configured_platforms = V2Config.configured_platforms.__get__(fake_config)

    assert fake_config.configured_platforms() == ["mockchat"]
    assert "mockchat" in IMFactory.get_supported_platforms()
