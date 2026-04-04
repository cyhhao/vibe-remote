"""E2E tests: API endpoints."""

from tests.e2e.http import JsonHttpClient


def _client(api_url: str) -> JsonHttpClient:
    return JsonHttpClient(api_url)


# Minimal valid config payload for POST /config
_VALID_CONFIG = {
    "mode": "self_host",
    "runtime": {"default_cwd": "/tmp/test"},
    "agents": {
        "default_backend": "opencode",
        "opencode": {"enabled": True, "cli_path": "opencode"},
        "claude": {"enabled": False, "cli_path": "claude"},
        "codex": {"enabled": False, "cli_path": "codex"},
    },
}


class TestConfigAPI:
    """Config CRUD via API."""

    def test_post_config_creates(self, api_url):
        """POST /config with valid payload should save and return config."""
        status, data = _client(api_url).post_json("/config", _VALID_CONFIG)
        assert status == 200
        assert data["mode"] == "self_host"
        assert data["runtime"]["default_cwd"] == "/tmp/test"

    def test_post_then_get_config(self, api_url):
        """GET /config should return config after it's been created via POST."""
        client = _client(api_url)
        client.post_json("/config", _VALID_CONFIG)
        status, data = client.get_json("/config")
        assert status == 200
        assert "mode" in data
        assert "version" in data

    def test_post_config_updates_cwd(self, api_url):
        """POST /config should update specific fields."""
        payload = {**_VALID_CONFIG, "runtime": {"default_cwd": "/tmp/e2e_updated"}}
        status, data = _client(api_url).post_json("/config", payload)
        assert status == 200
        assert data["runtime"]["default_cwd"] == "/tmp/e2e_updated"


class TestStatusAPI:
    """Service status endpoint."""

    def test_status_returns_json(self, api_url):
        status, data = _client(api_url).get_json("/status")
        assert status == 200
        assert "running" in data

    def test_status_shows_not_running(self, api_url):
        """In UI-only mode, service should not be running."""
        status, data = _client(api_url).get_json("/status")
        assert status == 200
        # In UI-only Docker mode, the service process is not started
        assert data["running"] is False


class TestDoctorAPI:
    """Doctor diagnostic endpoint."""

    def test_doctor_post(self, api_url):
        status, data = _client(api_url).post_json("/doctor")
        assert status == 200
        assert "groups" in data
        assert "summary" in data
        assert "ok" in data

    def test_doctor_get_after_post(self, api_url):
        """GET /doctor should return cached results after POST /doctor."""
        # First run doctor
        client = _client(api_url)
        client.post_json("/doctor")
        # Then fetch cached result
        status, data = client.get_json("/doctor")
        assert status == 200
        # Should have groups from the last run
        assert "groups" in data


class TestSlackManifest:
    """Slack manifest endpoint."""

    def test_get_manifest(self, api_url):
        status, data = _client(api_url).get_json("/slack/manifest")
        assert status == 200
        # Manifest is returned wrapped: {"manifest": "...", "ok": True}
        assert data.get("ok") is True
        assert "manifest" in data


class TestBrowseAPI:
    """Directory browsing endpoint."""

    def test_browse_root(self, api_url):
        status, data = _client(api_url).post_json("/browse", {"path": "/"})
        assert status == 200
        # Should return directory listing info
        assert "path" in data or "entries" in data or "dirs" in data


class TestCLIDetect:
    """CLI binary detection endpoint."""

    def test_detect_python(self, api_url):
        """Python should be detectable inside the container."""
        status, data = _client(api_url).get_json("/cli/detect?binary=python3")
        assert status == 200
        assert "found" in data or "path" in data
