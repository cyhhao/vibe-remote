"""E2E tests: Settings API endpoints."""

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


class TestSettingsAPI:
    """Settings CRUD via /settings endpoint."""

    def test_get_settings_returns_json(self, api_url):
        """GET /settings should return current settings."""
        status, data = _get(f"{api_url}/settings")
        assert status == 200
        assert isinstance(data, dict)

    def test_post_settings_roundtrip(self, api_url):
        """POST /settings should persist and return updated settings."""
        # Save settings with a channel config
        payload = {
            "channels": {
                "C_TEST_E2E": {
                    "enabled": True,
                    "show_message_types": ["system", "assistant"],
                }
            }
        }
        status, data = _post(f"{api_url}/settings", payload)
        assert status == 200

        # Verify the channel persisted
        status, data = _get(f"{api_url}/settings")
        assert status == 200
        channels = data.get("channels", {})
        assert "C_TEST_E2E" in channels
        assert channels["C_TEST_E2E"]["enabled"] is True


class TestLogsAPI:
    """Logs endpoint."""

    def test_logs_returns_structure(self, api_url):
        """POST /logs should return log lines and total count."""
        status, data = _post(f"{api_url}/logs", {"lines": 10})
        assert status == 200
        assert "source" in data
        assert "logs" in data
        assert "sources" in data
        assert "total" in data
        assert isinstance(data["logs"], list)
        assert isinstance(data["sources"], list)
        assert isinstance(data["total"], int)
