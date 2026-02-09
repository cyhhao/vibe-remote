"""OpenCode helper utilities.

Keep this module free of agent state. It should only contain pure helpers.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


_REASONING_FALLBACK_OPTIONS = [
    {"value": "low", "label": "Low"},
    {"value": "medium", "label": "Medium"},
    {"value": "high", "label": "High"},
]

_REASONING_VARIANT_ORDER = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]

_REASONING_VARIANT_LABELS = {
    "none": "None",
    "minimal": "Minimal",
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "xhigh": "Extra High",
    "max": "Max",
}

_UTILITY_MODEL_KEYWORDS = ["embedding", "tts", "whisper", "ada", "davinci", "turbo-instruct"]


def _parse_model_key(model_key: Optional[str]) -> tuple[str, str]:
    if not model_key:
        return "", ""
    parts = model_key.split("/", 1)
    if len(parts) != 2:
        return "", ""
    return parts[0], parts[1]


def _parse_provider_id(model_key: Optional[str]) -> Optional[str]:
    provider_id, _ = _parse_model_key(model_key)
    return provider_id or None


def _find_model_variants(opencode_models: dict, target_model: Optional[str]) -> Dict[str, Any]:
    target_provider, target_model_id = _parse_model_key(target_model)
    if not target_provider or not target_model_id or not isinstance(opencode_models, dict):
        return {}
    providers_data = opencode_models.get("providers", [])
    for provider in providers_data:
        provider_id = provider.get("id") or provider.get("provider_id") or provider.get("name")
        if provider_id != target_provider:
            continue

        models = provider.get("models", {})
        model_info: Optional[dict] = None
        if isinstance(models, dict):
            candidate = models.get(target_model_id)
            if isinstance(candidate, dict):
                model_info = candidate
        elif isinstance(models, list):
            for entry in models:
                if isinstance(entry, dict) and entry.get("id") == target_model_id:
                    model_info = entry
                    break

        if isinstance(model_info, dict):
            variants = model_info.get("variants", {})
            if isinstance(variants, dict):
                return variants
        break
    return {}


def _append_unique(target: List[str], value: Optional[str]) -> None:
    if not value or value in target:
        return
    target.append(value)


def _extract_provider_ids_from_config(config: dict) -> List[str]:
    providers: List[str] = []
    if not isinstance(config, dict):
        return providers

    provider_value = config.get("provider")
    if isinstance(provider_value, str):
        _append_unique(providers, provider_value)

    default_provider = config.get("default_provider")
    if isinstance(default_provider, str):
        _append_unique(providers, default_provider)

    config_providers = config.get("providers")
    if isinstance(config_providers, dict):
        for key in config_providers.keys():
            _append_unique(providers, key)
    elif isinstance(config_providers, list):
        for entry in config_providers:
            if isinstance(entry, str):
                _append_unique(providers, entry)
                continue
            if isinstance(entry, dict):
                value = entry.get("id") or entry.get("provider") or entry.get("name")
                if isinstance(value, str):
                    _append_unique(providers, value)

    return providers


def resolve_opencode_default_model(
    opencode_default_config: dict,
    opencode_agents: list,
    selected_agent: Optional[str],
) -> Optional[str]:
    """Resolve default OpenCode model for an agent from config."""
    agent_names: List[str] = []
    for agent in opencode_agents or []:
        if isinstance(agent, dict):
            name = agent.get("name") or agent.get("id")
        elif isinstance(agent, str):
            name = agent
        else:
            name = None
        if isinstance(name, str) and name:
            agent_names.append(name)

    agent_name = selected_agent or ("build" if "build" in agent_names else (agent_names[0] if agent_names else None))

    if isinstance(opencode_default_config, dict):
        agents_config = opencode_default_config.get("agent", {})
        if isinstance(agents_config, dict) and agent_name:
            agent_config = agents_config.get(agent_name, {})
            if isinstance(agent_config, dict):
                model = agent_config.get("model")
                if isinstance(model, str) and model:
                    return model
        model = opencode_default_config.get("model")
        if isinstance(model, str) and model:
            return model
    return None


def resolve_opencode_provider_preferences(
    opencode_default_config: dict,
    current_model: Optional[str] = None,
) -> List[str]:
    """Return provider IDs to prefer first when listing models."""
    providers: List[str] = []

    _append_unique(providers, _parse_provider_id(current_model))

    if isinstance(opencode_default_config, dict):
        _append_unique(providers, _parse_provider_id(opencode_default_config.get("model")))
        agents_config = opencode_default_config.get("agent", {})
        if isinstance(agents_config, dict):
            for agent_config in agents_config.values():
                if isinstance(agent_config, dict):
                    _append_unique(providers, _parse_provider_id(agent_config.get("model")))

        for provider_id in _extract_provider_ids_from_config(opencode_default_config):
            _append_unique(providers, provider_id)

    return providers


def resolve_opencode_allowed_providers(
    opencode_default_config: dict,
    opencode_models: Optional[dict] = None,
) -> List[str]:
    """Return provider IDs to include when listing models."""
    providers = _extract_provider_ids_from_config(opencode_default_config)
    if providers:
        return providers
    if isinstance(opencode_models, dict):
        defaults = opencode_models.get("default", {})
        if isinstance(defaults, dict) and defaults:
            return [key for key in defaults.keys() if isinstance(key, str) and key]
    return []


def _model_sort_key(model_item: Tuple[str, Any]) -> Tuple[int, int, str]:
    """Sort models by utility penalty, release date (DESC), then id."""
    model_id, model_info = model_item
    mid_lower = (model_id or "").lower()
    is_utility = any(keyword in mid_lower for keyword in _UTILITY_MODEL_KEYWORDS)
    utility_penalty = 1 if is_utility else 0
    release_date = "1970-01-01"
    if isinstance(model_info, dict):
        release_date = model_info.get("release_date", "1970-01-01") or "1970-01-01"
    try:
        date_int = -int(release_date.replace("-", ""))
    except (ValueError, AttributeError):
        date_int = 0
    return (utility_penalty, date_int, model_id or "")


def build_opencode_model_option_items(
    opencode_models: dict,
    max_total: int,
    preferred_providers: Optional[List[str]] = None,
    allowed_providers: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    """Build sorted model options for OpenCode providers."""
    if not isinstance(opencode_models, dict) or max_total <= 0:
        return []

    providers_data = opencode_models.get("providers", [])
    defaults = opencode_models.get("default", {})

    providers: List[Tuple[str, dict]] = []
    for provider in providers_data:
        provider_id = provider.get("id") or provider.get("provider_id") or provider.get("name") or ""
        if not provider_id:
            continue
        providers.append((provider_id, provider))

    if allowed_providers:
        allowed_set = {p for p in allowed_providers if isinstance(p, str) and p}
        if allowed_set:
            providers = [entry for entry in providers if entry[0] in allowed_set]

    if preferred_providers:
        preferred_set = {p for p in preferred_providers if isinstance(p, str) and p}
        if preferred_set:
            provider_map = {pid: provider for pid, provider in providers}
            ordered: List[Tuple[str, dict]] = []
            for pid in preferred_providers:
                provider = provider_map.get(pid)
                if provider is not None:
                    ordered.append((pid, provider))
            for pid, provider in providers:
                if pid not in preferred_set:
                    ordered.append((pid, provider))
            providers = ordered

    num_providers = len(providers)
    max_per_provider = max(5, (max_total // num_providers)) if num_providers > 0 else max_total

    options: List[Dict[str, str]] = []
    for provider_id, provider in providers:
        provider_name = provider.get("name") or provider_id
        models = provider.get("models", {})

        if isinstance(models, dict):
            model_items = list(models.items())
        elif isinstance(models, list):
            model_items = [
                (entry, entry) if isinstance(entry, str) else (entry.get("id", ""), entry)
                for entry in models
            ]
        else:
            model_items = []

        model_items.sort(key=_model_sort_key)
        provider_model_count = 0
        for model_id, model_info in model_items:
            if provider_model_count >= max_per_provider:
                break
            if not model_id:
                continue

            if isinstance(model_info, dict):
                model_name = model_info.get("name", model_id) or model_id
            else:
                model_name = model_id

            full_model = f"{provider_id}/{model_id}" if provider_id else model_id
            is_default = defaults.get(provider_id) == model_id if provider_id else False
            display = f"{provider_name}: {model_name}" if provider_name else model_name
            if is_default:
                display += " (default)"

            options.append({"label": display, "value": full_model})
            provider_model_count += 1

    if len(options) > max_total:
        options = options[:max_total]
    return options


def _build_reasoning_options_from_variants(variants: Dict[str, Any]) -> List[Dict[str, str]]:
    sorted_variants = sorted(
        variants.keys(),
        key=lambda variant: (
            _REASONING_VARIANT_ORDER.index(variant)
            if variant in _REASONING_VARIANT_ORDER
            else len(_REASONING_VARIANT_ORDER),
            variant,
        ),
    )
    return [
        {
            "value": variant_key,
            "label": _REASONING_VARIANT_LABELS.get(variant_key, variant_key.capitalize()),
        }
        for variant_key in sorted_variants
    ]


def build_reasoning_effort_options(
    opencode_models: dict,
    target_model: Optional[str],
) -> List[Dict[str, str]]:
    """Build reasoning effort options from OpenCode model metadata."""

    options = [{"value": "__default__", "label": "(Default)"}]
    variants = _find_model_variants(opencode_models, target_model)
    if variants:
        options.extend(_build_reasoning_options_from_variants(variants))
        return options
    options.extend(_REASONING_FALLBACK_OPTIONS)
    return options
