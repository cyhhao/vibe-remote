from __future__ import annotations

import sys
import types
import importlib
import ast
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.v2_config import AgentsConfig, RuntimeConfig, SlackConfig, V2Config
from vibe import sentry_integration


def _config() -> V2Config:
    return V2Config(
        mode="self_host",
        version="v2",
        slack=SlackConfig(bot_token=""),
        runtime=RuntimeConfig(default_cwd="/tmp/workdir", log_level="INFO"),
        agents=AgentsConfig(),
    )


def test_resolve_sentry_options_prefers_environment(monkeypatch):
    monkeypatch.setenv("VIBE_SENTRY_DSN", "https://env@example.ingest.sentry.io/2")
    monkeypatch.setenv("VIBE_DEPLOYMENT_ENV", "regression")
    monkeypatch.setenv("VIBE_SENTRY_TRACES_SAMPLE_RATE", "0.25")

    options = sentry_integration.resolve_sentry_options()

    assert options is not None
    assert options["dsn"] == "https://env@example.ingest.sentry.io/2"
    assert options["environment"] == "regression"
    assert options["traces_sample_rate"] == 0.25


def test_resolve_sentry_options_returns_none_without_dsn(monkeypatch):
    monkeypatch.delenv("VIBE_SENTRY_DSN", raising=False)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.setattr(sentry_integration, "DEFAULT_SENTRY_DSN", "")

    assert sentry_integration.resolve_sentry_options() is None


def test_resolve_sentry_options_honors_empty_env_dsn_as_opt_out(monkeypatch):
    monkeypatch.setenv("VIBE_SENTRY_DSN", "")
    monkeypatch.setattr(sentry_integration, "DEFAULT_SENTRY_DSN", "https://default@example.ingest.sentry.io/1")

    assert sentry_integration.resolve_sentry_options() is None


def test_detect_sentry_environment_defaults_to_development(monkeypatch):
    monkeypatch.delenv("VIBE_SENTRY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("SENTRY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("VIBE_DEPLOYMENT_ENV", raising=False)
    monkeypatch.setenv("VIBE_REMOTE_HOME", "/tmp/vibe-remote-home")
    monkeypatch.setattr(sentry_integration, "Path", lambda _: type("P", (), {"exists": staticmethod(lambda: False)})())

    assert sentry_integration.detect_sentry_environment() == "development"


def test_detect_sentry_environment_uses_explicit_deployment_env(monkeypatch):
    monkeypatch.delenv("VIBE_SENTRY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("SENTRY_ENVIRONMENT", raising=False)
    monkeypatch.setenv("VIBE_DEPLOYMENT_ENV", "production")

    assert sentry_integration.detect_sentry_environment() == "production"


def test_build_sentry_contexts_contains_debug_metadata(monkeypatch):
    monkeypatch.setenv("VIBE_REMOTE_HOME", "/tmp/vibe-remote-home")

    contexts = sentry_integration.build_sentry_contexts(_config(), component="service", environment="regression")

    assert contexts["deployment"]["environment"] == "regression"
    assert contexts["deployment"]["component"] == "service"
    assert contexts["deployment"]["default_backend"] == "opencode"
    assert contexts["runtime"]["python_version"]
    assert "hostname" in contexts["host"]


def test_init_sentry_returns_false_when_sdk_init_raises(monkeypatch):
    monkeypatch.setenv("VIBE_SENTRY_DSN", "https://env@example.ingest.sentry.io/2")

    sentry_sdk = types.ModuleType("sentry_sdk")
    sentry_sdk.init = lambda **kwargs: (_ for _ in ()).throw(ValueError("bad dsn"))
    sentry_sdk.set_tag = lambda *args, **kwargs: None
    sentry_sdk.set_context = lambda *args, **kwargs: None

    logging_integration_module = types.ModuleType("sentry_sdk.integrations.logging")

    class FakeLoggingIntegration:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    logging_integration_module.LoggingIntegration = FakeLoggingIntegration

    monkeypatch.setitem(sys.modules, "sentry_sdk", sentry_sdk)
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations.logging", logging_integration_module)

    assert sentry_integration.init_sentry(_config(), component="service") is False


def test_run_ui_server_skips_sentry_when_config_load_fails(monkeypatch):
    fake_flask = types.ModuleType("flask")

    class FakeFlask:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def route(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

        def errorhandler(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

    fake_flask.Flask = FakeFlask
    fake_flask.request = types.SimpleNamespace(json=None, args={})
    fake_flask.jsonify = lambda *args, **kwargs: {"args": args, "kwargs": kwargs}
    fake_flask.send_file = lambda *args, **kwargs: None
    fake_flask.Response = object
    monkeypatch.setitem(sys.modules, "flask", fake_flask)

    fake_werkzeug = types.ModuleType("werkzeug")
    fake_werkzeug_serving = types.ModuleType("werkzeug.serving")

    ui_server = importlib.import_module("vibe.ui_server")

    class DummyServer:
        def serve_forever(self):
            return None

    fake_werkzeug_serving.make_server = lambda *args, **kwargs: DummyServer()
    monkeypatch.setitem(sys.modules, "werkzeug", fake_werkzeug)
    monkeypatch.setitem(sys.modules, "werkzeug.serving", fake_werkzeug_serving)

    monkeypatch.setattr(ui_server.paths, "ensure_data_dirs", lambda: None)
    monkeypatch.setattr(
        ui_server.V2Config,
        "load",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad config")),
    )
    sentry_calls = []
    monkeypatch.setattr(ui_server, "init_sentry", lambda *args, **kwargs: sentry_calls.append((args, kwargs)))

    ui_server.run_ui_server("127.0.0.1", 0)

    assert sentry_calls == []


def test_ui_error_handler_does_not_explicitly_capture_exceptions():
    source = Path("vibe/ui_server.py").read_text(encoding="utf-8")
    module = ast.parse(source)

    handle_exception = next(
        node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "handle_exception"
    )

    calls_capture_exception = False
    for node in ast.walk(handle_exception):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "capture_exception":
            calls_capture_exception = True
            break

    assert calls_capture_exception is False


def test_scrub_data_redacts_sensitive_values():
    event = {
        "request": {
            "headers": {"Authorization": "Bearer abc", "Cookie": "session=secret"},
            "data": {"bot_token": "xoxb-secret", "nested": [{"client_secret": "shh"}]},
        },
        "message": "token=abc123 and xapp-123456",
    }

    scrubbed = sentry_integration.scrub_data(event)

    assert scrubbed["request"]["headers"]["Authorization"] == "[Filtered]"
    assert scrubbed["request"]["headers"]["Cookie"] == "[Filtered]"
    assert scrubbed["request"]["data"]["bot_token"] == "[Filtered]"
    assert scrubbed["request"]["data"]["nested"][0]["client_secret"] == "[Filtered]"
    assert "[Filtered]" in scrubbed["message"]
