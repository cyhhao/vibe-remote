"""OpenCode helper utilities.

Keep this module free of agent state. It should only contain pure helpers.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


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


def _parse_model_key(model_key: Optional[str]) -> tuple[str, str]:
    if not model_key:
        return "", ""
    parts = model_key.split("/", 1)
    if len(parts) != 2:
        return "", ""
    return parts[0], parts[1]


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
