from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

from config.v2_config import V2Config

logger = logging.getLogger(__name__)

_SENSITIVE_KEY_PARTS = (
    "access_key",
    "api_key",
    "app_token",
    "authorization",
    "bot_token",
    "client_secret",
    "cookie",
    "dsn",
    "password",
    "secret",
    "signing_secret",
    "token",
    "webhook",
    "workspace_token",
)
_REDACTED = "[Filtered]"
_STRING_REPLACEMENTS = (
    (re.compile(r"(?i)(authorization[:=]\s*)(bearer\s+)?\S+"), r"\1" + _REDACTED),
    (re.compile(r"(?i)(token=)[^&\s]+"), r"\1" + _REDACTED),
    (re.compile(r"(?i)(cookie[:=]\s*)\S+"), r"\1" + _REDACTED),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]+\b"), _REDACTED),
    (re.compile(r"\bxapp-[A-Za-z0-9-]+\b"), _REDACTED),
)


def _redact_string(value: str) -> str:
    redacted = value
    for pattern, replacement in _STRING_REPLACEMENTS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def scrub_data(value: Any) -> Any:
    if isinstance(value, dict):
        scrubbed: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _is_sensitive_key(key):
                scrubbed[key] = _REDACTED
            else:
                scrubbed[key] = scrub_data(item)
        return scrubbed
    if isinstance(value, list):
        return [scrub_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_data(item) for item in value)
    if isinstance(value, str):
        return _redact_string(value)
    return value


def _safe_float(raw: Optional[str], fallback: float) -> float:
    if raw is None or raw == "":
        return fallback
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid Sentry sample rate %r, falling back to %s", raw, fallback)
        return fallback
    if 0.0 <= value <= 1.0:
        return value
    logger.warning("Out-of-range Sentry sample rate %r, falling back to %s", raw, fallback)
    return fallback


def resolve_sentry_options(config: V2Config) -> Optional[dict[str, Any]]:
    sentry_config = getattr(config, "sentry", None)
    env_dsn = os.environ.get("VIBE_SENTRY_DSN") or os.environ.get("SENTRY_DSN")
    dsn = env_dsn or (sentry_config.dsn if sentry_config else None)
    if not dsn:
        return None

    environment = (
        os.environ.get("VIBE_SENTRY_ENVIRONMENT")
        or os.environ.get("SENTRY_ENVIRONMENT")
        or (sentry_config.environment if sentry_config else None)
        or config.mode
    )
    traces_sample_rate = _safe_float(
        os.environ.get("VIBE_SENTRY_TRACES_SAMPLE_RATE"),
        sentry_config.traces_sample_rate if sentry_config else 0.0,
    )
    profiles_sample_rate = _safe_float(
        os.environ.get("VIBE_SENTRY_PROFILES_SAMPLE_RATE"),
        sentry_config.profiles_sample_rate if sentry_config else 0.0,
    )
    return {
        "dsn": dsn,
        "environment": environment,
        "traces_sample_rate": traces_sample_rate,
        "profiles_sample_rate": profiles_sample_rate,
        "send_default_pii": bool(sentry_config.send_default_pii) if sentry_config else False,
    }


def before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    del hint
    return scrub_data(event)


def before_breadcrumb(crumb: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    del hint
    return scrub_data(crumb)


def init_sentry(config: V2Config, component: str, enable_flask: bool = False) -> bool:
    options = resolve_sentry_options(config)
    if not options:
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except Exception as exc:
        logger.warning("Sentry initialization skipped: %s", exc)
        return False

    integrations: list[Any] = [
        LoggingIntegration(
            level=logging.WARNING,
            event_level=logging.ERROR,
        )
    ]
    if enable_flask:
        try:
            from sentry_sdk.integrations.flask import FlaskIntegration
        except Exception as exc:
            logger.warning("Flask Sentry integration unavailable: %s", exc)
        else:
            integrations.append(FlaskIntegration())

    from vibe import __version__

    sentry_sdk.init(
        dsn=options["dsn"],
        environment=options["environment"],
        release=f"vibe-remote@{__version__}",
        integrations=integrations,
        before_send=before_send,
        before_breadcrumb=before_breadcrumb,
        attach_stacktrace=True,
        ignore_errors=[KeyboardInterrupt, SystemExit],
        max_breadcrumbs=50,
        traces_sample_rate=options["traces_sample_rate"],
        profiles_sample_rate=options["profiles_sample_rate"],
        send_default_pii=options["send_default_pii"],
    )
    sentry_sdk.set_tag("component", component)
    sentry_sdk.set_tag("mode", config.mode)
    sentry_sdk.set_tag("primary_platform", config.platforms.primary)
    sentry_sdk.set_context(
        "vibe_remote",
        {
            "enabled_platforms": config.platforms.enabled,
            "default_backend": config.agents.default_backend,
        },
    )
    logger.info("Sentry initialized for %s (environment=%s)", component, options["environment"])
    return True


def capture_exception(exc: Exception) -> None:
    try:
        import sentry_sdk
    except Exception:
        return
    sentry_sdk.capture_exception(exc)
