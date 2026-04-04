"""E2E tests: Settings API endpoints."""

from tests.e2e.http import JsonHttpClient


def _client(api_url: str) -> JsonHttpClient:
    return JsonHttpClient(api_url)


class TestSettingsAPI:
    """Settings CRUD via /settings endpoint."""

    def test_get_settings_returns_json(self, api_url):
        """GET /settings should return current settings."""
        status, data = _client(api_url).get_json("/settings")
        assert status == 200
        assert isinstance(data, dict)

    def test_post_settings_roundtrip(self, api_url):
        """POST /settings should persist and return updated settings."""
        client = _client(api_url)
        # Save settings with a channel config
        payload = {
            "channels": {
                "C_TEST_E2E": {
                    "enabled": True,
                    "show_message_types": ["system", "assistant"],
                }
            }
        }
        status, data = client.post_json("/settings", payload)
        assert status == 200

        # Verify the channel persisted
        status, data = client.get_json("/settings")
        assert status == 200
        channels = data.get("channels", {})
        assert "C_TEST_E2E" in channels
        assert channels["C_TEST_E2E"]["enabled"] is True


class TestLogsAPI:
    """Logs endpoint."""

    def test_logs_returns_structure(self, api_url):
        """POST /logs should return log lines and total count."""
        status, data = _client(api_url).post_json("/logs", {"lines": 10})
        assert status == 200
        assert "source" in data
        assert "logs" in data
        assert "sources" in data
        assert "total" in data
        assert isinstance(data["logs"], list)
        assert isinstance(data["sources"], list)
        assert isinstance(data["total"], int)
