"""Reconcile OpenCode user config, auth storage, and live runtime config."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Dict, Mapping

LOCAL_PROVIDER_IDS = {"ollama", "lmstudio", "lm-studio"}
USER_MODEL_META_KEY = "vibe_remote"


@dataclass(frozen=True)
class OpenCodeConfigReconciler:
    """Build the config payload that should be applied to live OpenCode.

    ``opencode.json`` is the source of truth for user-managed config.
    ``auth.json`` is the source of truth for provider credentials.
    The live OpenCode config is only a preservation source for providers
    OpenCode owns at runtime, currently auth-backed and local providers.
    """

    local_provider_ids: frozenset[str] = frozenset(LOCAL_PROVIDER_IDS)

    def reconcile(
        self,
        *,
        user_config: Mapping[str, Any],
        live_config: Mapping[str, Any],
        auth_entries: Mapping[str, Mapping[str, Any]],
    ) -> Dict[str, Any]:
        merged = copy.deepcopy(dict(user_config))
        user_providers = user_config.get("provider")
        if not isinstance(user_providers, Mapping):
            if "provider" in user_config:
                merged.pop("provider", None)
            else:
                live_providers = live_config.get("provider")
                live_providers = live_providers if isinstance(live_providers, Mapping) else {}
                preserved = self._reconcile_providers(
                    user_providers={},
                    live_providers=live_providers,
                    auth_entries=auth_entries,
                )
                if preserved:
                    merged["provider"] = preserved
            return merged

        live_providers = live_config.get("provider")
        live_providers = live_providers if isinstance(live_providers, Mapping) else {}
        merged["provider"] = self._reconcile_providers(
            user_providers=user_providers,
            live_providers=live_providers,
            auth_entries=auth_entries,
        )
        return merged

    def _reconcile_providers(
        self,
        *,
        user_providers: Mapping[str, Any],
        live_providers: Mapping[str, Any],
        auth_entries: Mapping[str, Mapping[str, Any]],
    ) -> Dict[str, Any]:
        merged: Dict[str, Any] = {}
        for provider_id, user_provider in user_providers.items():
            if not isinstance(provider_id, str) or not isinstance(user_provider, Mapping):
                continue
            merged[provider_id] = self._reconcile_user_provider(
                provider_id=provider_id,
                user_provider=user_provider,
                live_provider=live_providers.get(provider_id),
                auth_entry=auth_entries.get(provider_id),
            )

        for provider_id, auth_entry in auth_entries.items():
            if provider_id in merged or not isinstance(provider_id, str) or not isinstance(auth_entry, Mapping):
                continue
            live_provider = live_providers.get(provider_id)
            if isinstance(live_provider, Mapping):
                merged[provider_id] = self._copy_auth_backed_live_provider(
                    live_provider=live_provider,
                    auth_entry=auth_entry,
                )
            else:
                auth_api_key = self._auth_api_key(auth_entry)
                if auth_api_key:
                    merged[provider_id] = {"options": {"apiKey": auth_api_key}}

        for provider_id, live_provider in live_providers.items():
            if provider_id in merged or not isinstance(provider_id, str) or not isinstance(live_provider, Mapping):
                continue
            if self._is_local_provider(provider_id):
                merged[provider_id] = self._copy_live_local_provider(live_provider)

        return merged

    def _reconcile_user_provider(
        self,
        *,
        provider_id: str,
        user_provider: Mapping[str, Any],
        live_provider: Any,
        auth_entry: Mapping[str, Any] | None,
    ) -> Dict[str, Any]:
        if isinstance(live_provider, Mapping):
            provider = self._deep_merge_config(dict(live_provider), user_provider)
        else:
            provider = copy.deepcopy(dict(user_provider))

        is_local_provider = self._is_local_provider(provider_id)
        self._reconcile_user_provider_options(provider, user_provider, auth_entry, is_local_provider)
        self._reconcile_user_provider_models(provider, user_provider, is_local_provider)
        return provider

    def _reconcile_user_provider_options(
        self,
        provider: Dict[str, Any],
        user_provider: Mapping[str, Any],
        auth_entry: Mapping[str, Any] | None,
        is_local_provider: bool,
    ) -> None:
        user_options = user_provider.get("options")
        auth_api_key = self._auth_api_key(auth_entry)
        if isinstance(user_options, Mapping):
            options = copy.deepcopy(dict(user_options))
            if auth_api_key and "apiKey" not in options:
                options["apiKey"] = auth_api_key
            provider["options"] = options
            return

        if "options" in user_provider:
            provider.pop("options", None)
            return

        if auth_api_key:
            provider["options"] = {"apiKey": auth_api_key}
            return

        if is_local_provider and isinstance(provider.get("options"), Mapping):
            return

        provider.pop("options", None)

    @staticmethod
    def _reconcile_user_provider_models(
        provider: Dict[str, Any],
        user_provider: Mapping[str, Any],
        is_local_provider: bool,
    ) -> None:
        user_models = user_provider.get("models")
        if isinstance(user_models, Mapping):
            if is_local_provider and isinstance(provider.get("models"), Mapping):
                provider["models"] = OpenCodeConfigReconciler._merge_local_provider_models(
                    provider["models"],
                    user_models,
                )
            else:
                provider["models"] = copy.deepcopy(dict(user_models))
        elif "models" in user_provider:
            provider.pop("models", None)

    @staticmethod
    def _merge_local_provider_models(live_models: Mapping[str, Any], user_models: Mapping[str, Any]) -> Dict[str, Any]:
        merged: Dict[str, Any] = {}
        for model_id, model_info in live_models.items():
            if not isinstance(model_id, str):
                continue
            if isinstance(model_info, Mapping) and OpenCodeConfigReconciler._is_explicit_user_model(model_info):
                continue
            merged[model_id] = copy.deepcopy(model_info)
        merged.update(copy.deepcopy(dict(user_models)))
        return merged

    @staticmethod
    def _copy_live_local_provider(live_provider: Mapping[str, Any]) -> Dict[str, Any]:
        provider = copy.deepcopy(dict(live_provider))
        models = provider.get("models")
        if isinstance(models, Mapping):
            provider["models"] = OpenCodeConfigReconciler._merge_local_provider_models(models, {})
        return provider

    @staticmethod
    def _is_explicit_user_model(model_info: Mapping[str, Any]) -> bool:
        meta = model_info.get(USER_MODEL_META_KEY)
        return isinstance(meta, Mapping) and meta.get("user_model") is True

    def _copy_auth_backed_live_provider(
        self,
        *,
        live_provider: Mapping[str, Any],
        auth_entry: Mapping[str, Any],
    ) -> Dict[str, Any]:
        provider = copy.deepcopy(dict(live_provider))
        auth_api_key = self._auth_api_key(auth_entry)
        options = provider.get("options")
        if auth_api_key:
            if not isinstance(options, Mapping):
                options = {}
            else:
                options = copy.deepcopy(dict(options))
            options["apiKey"] = auth_api_key
            provider["options"] = options
        elif auth_entry.get("type") == "oauth" and isinstance(options, Mapping):
            options = copy.deepcopy(dict(options))
            options.pop("apiKey", None)
            if options:
                provider["options"] = options
            else:
                provider.pop("options", None)
        return provider

    def _is_local_provider(self, provider_id: str) -> bool:
        return provider_id.lower() in self.local_provider_ids

    @staticmethod
    def _auth_api_key(auth_entry: Mapping[str, Any] | None) -> str | None:
        if not isinstance(auth_entry, Mapping) or auth_entry.get("type") != "api":
            return None
        key = auth_entry.get("key")
        return key if isinstance(key, str) and key else None

    @classmethod
    def _deep_merge_config(cls, base: Any, override: Any) -> Any:
        if not isinstance(base, Mapping) or not isinstance(override, Mapping):
            return copy.deepcopy(override)
        merged = copy.deepcopy(dict(base))
        for key, value in override.items():
            current = merged.get(key)
            if isinstance(current, Mapping) and isinstance(value, Mapping):
                merged[key] = cls._deep_merge_config(current, value)
            else:
                merged[key] = copy.deepcopy(value)
        return merged
