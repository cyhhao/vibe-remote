from __future__ import annotations

import logging
import os
import platform
import re
import socket
import sys
from pathlib import Path
from typing import Any, Optional

from config.v2_config import V2Config

logger = logging.getLogger(__name__)

# Fill this with the real project DSN to make Sentry default-on in production.
DEFAULT_SENTRY_DSN = ""
DEFAULT_TRACES_SAMPLE_RATE = 0.0
DEFAULT_PROFILES_SAMPLE_RATE = 0.0
DEPLOYMENT_ENV_VAR = "VIBE_DEPLOYMENT_ENV"

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


def detect_sentry_environment() -> str:
    explicit = os.environ.get("VIBE_SENTRY_ENVIRONMENT") or os.environ.get("SENTRY_ENVIRONMENT")
    if explicit:
        return explicit.strip()

    deployment = os.environ.get(DEPLOYMENT_ENV_VAR)
    if deployment:
        return deployment.strip()

    vibe_home = os.environ.get("VIBE_REMOTE_HOME", "")
    if "three-regression" in vibe_home:
        return "regression"

    if Path("/.dockerenv").exists():
        return "production"

    return "development"


def resolve_sentry_options() -> Optional[dict[str, Any]]:
    dsn = os.environ.get("VIBE_SENTRY_DSN") or os.environ.get("SENTRY_DSN") or DEFAULT_SENTRY_DSN
    if not dsn:
        return None

    return {
        "dsn": dsn,
        "environment": detect_sentry_environment(),
        "traces_sample_rate": _safe_float(
            os.environ.get("VIBE_SENTRY_TRACES_SAMPLE_RATE"),
            DEFAULT_TRACES_SAMPLE_RATE,
        ),
        "profiles_sample_rate": _safe_float(
            os.environ.get("VIBE_SENTRY_PROFILES_SAMPLE_RATE"),
            DEFAULT_PROFILES_SAMPLE_RATE,
        ),
    }


def build_sentry_contexts(config: V2Config, component: str, environment: str) -> dict[str, dict[str, Any]]:
    vibe_home = os.environ.get("VIBE_REMOTE_HOME") or str(Path.home() / ".vibe_remote")
    return {
        "runtime": {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "platform": sys.platform,
        },
        "host": {
            "hostname": socket.gethostname(),
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor() or None,
            "docker": Path("/.dockerenv").exists(),
        },
        "deployment": {
            "component": component,
            "environment": environment,
            "mode": config.mode,
            "primary_platform": config.platforms.primary,
            "enabled_platforms": config.platforms.enabled,
            "default_backend": config.agents.default_backend,
            "cwd": config.runtime.default_cwd,
            "vibe_home": vibe_home,
        },
    }


def before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    del hint
    return scrub_data(event)


def before_breadcrumb(crumb: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    del hint
    return scrub_data(crumb)


def init_sentry(config: V2Config, component: str, enable_flask: bool = False) -> bool:
    options = resolve_sentry_options()
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
        sample_rate=1.0,
        traces_sample_rate=options["traces_sample_rate"],
        profiles_sample_rate=options["profiles_sample_rate"],
        send_default_pii=True,
        server_name=socket.gethostname(),
    )
    sentry_sdk.set_tag("component", component)
    sentry_sdk.set_tag("mode", config.mode)
    sentry_sdk.set_tag("primary_platform", config.platforms.primary)
    sentry_sdk.set_tag("deployment_environment", options["environment"])
    for name, context in build_sentry_contexts(config, component, options["environment"]).items():
        sentry_sdk.set_context(name, context)
    logger.info("Sentry initialized for %s (environment=%s)", component, options["environment"])
    return True


def capture_exception(exc: Exception) -> None:
    try:
        import sentry_sdk
    except Exception:
        return
    sentry_sdk.capture_exception(exc)
