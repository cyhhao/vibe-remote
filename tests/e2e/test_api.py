"""E2E tests: API endpoints."""

import json
import urllib.request


def _get(url, timeout=5):
    resp = urllib.request.urlopen(url, timeout=timeout)
    return resp.status, json.loads(resp.read())


def _post(url, body=None, timeout=5):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=timeout)
    return resp.status, json.loads(resp.read())


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
        status, data = _post(f"{api_url}/config", _VALID_CONFIG)
        assert status == 200
        assert data["mode"] == "self_host"
        assert data["runtime"]["default_cwd"] == "/tmp/test"

    def test_post_then_get_config(self, api_url):
        """GET /config should return config after it's been created via POST."""
        _post(f"{api_url}/config", _VALID_CONFIG)
        status, data = _get(f"{api_url}/config")
        assert status == 200
        assert "mode" in data
        assert "version" in data

    def test_post_config_updates_cwd(self, api_url):
        """POST /config should update specific fields."""
        payload = {**_VALID_CONFIG, "runtime": {"default_cwd": "/tmp/e2e_updated"}}
        status, data = _post(f"{api_url}/config", payload)
        assert status == 200
        assert data["runtime"]["default_cwd"] == "/tmp/e2e_updated"


class TestStatusAPI:
    """Service status endpoint."""

    def test_status_returns_json(self, api_url):
        status, data = _get(f"{api_url}/status")
        assert status == 200
        assert "running" in data

    def test_status_shows_not_running(self, api_url):
        """In UI-only mode, service should not be running."""
        status, data = _get(f"{api_url}/status")
        assert status == 200
        # In UI-only Docker mode, the service process is not started
        assert data["running"] is False


class TestDoctorAPI:
    """Doctor diagnostic endpoint."""

    def test_doctor_post(self, api_url):
        status, data = _post(f"{api_url}/doctor")
        assert status == 200
        assert "groups" in data
        assert "summary" in data
        assert "ok" in data

    def test_doctor_get_after_post(self, api_url):
        """GET /doctor should return cached results after POST /doctor."""
        # First run doctor
        _post(f"{api_url}/doctor")
        # Then fetch cached result
        status, data = _get(f"{api_url}/doctor")
        assert status == 200
        # Should have groups from the last run
        assert "groups" in data


class TestSlackManifest:
    """Slack manifest endpoint."""

    def test_get_manifest(self, api_url):
        status, data = _get(f"{api_url}/slack/manifest")
        assert status == 200
        # Manifest is returned wrapped: {"manifest": "...", "ok": True}
        assert data.get("ok") is True
        assert "manifest" in data


class TestBrowseAPI:
    """Directory browsing endpoint."""

    def test_browse_root(self, api_url):
        status, data = _post(f"{api_url}/browse", {"path": "/"})
        assert status == 200
        # Should return directory listing info
        assert "path" in data or "entries" in data or "dirs" in data


class TestCLIDetect:
    """CLI binary detection endpoint."""

    def test_detect_python(self, api_url):
        """Python should be detectable inside the container."""
        status, data = _get(f"{api_url}/cli/detect?binary=python3")
        assert status == 200
        assert "found" in data or "path" in data
