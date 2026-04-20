"""Single source of truth for IM platform metadata and capabilities."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from importlib import import_module
from typing import Any


@dataclass(frozen=True)
class PlatformCapabilities:
    supports_channels: bool
    supports_threads: bool
    supports_buttons: bool
    supports_quick_replies: bool
    supports_message_editing: bool
    markdown_upload_returns_message_id: bool = False
    quick_reply_single_column: bool = False


@dataclass(frozen=True)
class PlatformDescriptor:
    id: str
    config_key: str
    config_module: str
    config_class: str
    client_module: str
    client_class: str
    formatter_module: str
    formatter_class: str
    credential_fields: tuple[str, ...]
    capabilities: PlatformCapabilities

    @property
    def title_key(self) -> str:
        return f"platform.{self.id}.title"

    @property
    def description_key(self) -> str:
        return f"platform.{self.id}.desc"

    def get_config(self, app_config: Any) -> Any:
        platform_configs = getattr(app_config, "platform_configs", None)
        if isinstance(platform_configs, dict) and self.id in platform_configs:
            return platform_configs[self.id]
        return getattr(app_config, self.config_key, None)

    def get_config_class(self) -> type[Any]:
        return _load_attr(self.config_module, self.config_class)

    def create_config(self, payload: dict[str, Any]) -> Any:
        config_cls = self.get_config_class()
        valid_fields = {field.name for field in fields(config_cls)}
        platform_config = config_cls(**{key: value for key, value in payload.items() if key in valid_fields})
        validate = getattr(platform_config, "validate", None)
        if callable(validate):
            validate()
        return platform_config

    def has_credentials(self, app_config: Any) -> bool:
        platform_config = self.get_config(app_config)
        if platform_config is None:
            return False
        return all(bool(getattr(platform_config, field, None)) for field in self.credential_fields)

    def create_client(self, app_config: Any) -> Any:
        platform_config = self.get_config(app_config)
        if platform_config is None:
            raise ValueError(f"{self.id.title()} configuration not found")
        client_cls = _load_attr(self.client_module, self.client_class)
        return client_cls(platform_config)

    def create_formatter(self) -> Any:
        formatter_cls = _load_attr(self.formatter_module, self.formatter_class)
        return formatter_cls()

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "config_key": self.config_key,
            "title_key": self.title_key,
            "description_key": self.description_key,
            "credential_fields": list(self.credential_fields),
            "capabilities": asdict(self.capabilities),
        }


def _load_attr(module_name: str, attr_name: str) -> Any:
    module = import_module(module_name)
    return getattr(module, attr_name)


PLATFORM_REGISTRY: dict[str, PlatformDescriptor] = {
    "slack": PlatformDescriptor(
        id="slack",
        config_key="slack",
        config_module="config.v2_config",
        config_class="SlackConfig",
        client_module="modules.im.slack",
        client_class="SlackBot",
        formatter_module="modules.im.formatters",
        formatter_class="SlackFormatter",
        credential_fields=("bot_token",),
        capabilities=PlatformCapabilities(
            supports_channels=True,
            supports_threads=True,
            supports_buttons=True,
            supports_quick_replies=True,
            supports_message_editing=True,
        ),
    ),
    "discord": PlatformDescriptor(
        id="discord",
        config_key="discord",
        config_module="config.v2_config",
        config_class="DiscordConfig",
        client_module="modules.im.discord",
        client_class="DiscordBot",
        formatter_module="modules.im.formatters",
        formatter_class="DiscordFormatter",
        credential_fields=("bot_token",),
        capabilities=PlatformCapabilities(
            supports_channels=True,
            supports_threads=True,
            supports_buttons=True,
            supports_quick_replies=True,
            supports_message_editing=True,
            markdown_upload_returns_message_id=True,
        ),
    ),
    "telegram": PlatformDescriptor(
        id="telegram",
        config_key="telegram",
        config_module="config.v2_config",
        config_class="TelegramConfig",
        client_module="modules.im.telegram",
        client_class="TelegramBot",
        formatter_module="modules.im.formatters",
        formatter_class="TelegramFormatter",
        credential_fields=("bot_token",),
        capabilities=PlatformCapabilities(
            supports_channels=True,
            supports_threads=False,
            supports_buttons=True,
            supports_quick_replies=True,
            supports_message_editing=True,
            markdown_upload_returns_message_id=True,
            quick_reply_single_column=True,
        ),
    ),
    "lark": PlatformDescriptor(
        id="lark",
        config_key="lark",
        config_module="config.v2_config",
        config_class="LarkConfig",
        client_module="modules.im.feishu",
        client_class="FeishuBot",
        formatter_module="modules.im.formatters",
        formatter_class="FeishuFormatter",
        credential_fields=("app_id", "app_secret"),
        capabilities=PlatformCapabilities(
            supports_channels=True,
            supports_threads=True,
            supports_buttons=True,
            supports_quick_replies=True,
            supports_message_editing=True,
            markdown_upload_returns_message_id=True,
            quick_reply_single_column=True,
        ),
    ),
    "wechat": PlatformDescriptor(
        id="wechat",
        config_key="wechat",
        config_module="config.v2_config",
        config_class="WeChatConfig",
        client_module="modules.im.wechat",
        client_class="WeChatBot",
        formatter_module="modules.im.formatters",
        formatter_class="WeChatFormatter",
        credential_fields=("bot_token",),
        capabilities=PlatformCapabilities(
            supports_channels=False,
            supports_threads=False,
            supports_buttons=False,
            supports_quick_replies=False,
            supports_message_editing=False,
        ),
    ),
}


def platform_descriptors() -> list[PlatformDescriptor]:
    return list(PLATFORM_REGISTRY.values())


def supported_platform_ids() -> list[str]:
    return list(PLATFORM_REGISTRY.keys())


def supported_platform_set() -> set[str]:
    return set(PLATFORM_REGISTRY)


def get_platform_descriptor(platform: str) -> PlatformDescriptor:
    try:
        return PLATFORM_REGISTRY[platform]
    except KeyError as err:
        raise ValueError(f"Unsupported platform: {platform}") from err


def platform_catalog_payload() -> list[dict[str, Any]]:
    return [descriptor.to_public_dict() for descriptor in platform_descriptors()]
