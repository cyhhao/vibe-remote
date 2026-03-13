"""E2E tests: health and basic startup verification."""

import json
import urllib.request


class TestHealth:
    """Verify the container starts and responds."""

    def test_health_endpoint(self, api_url):
        resp = urllib.request.urlopen(f"{api_url}/health", timeout=5)
        assert resp.status == 200
        data = json.loads(resp.read())
        assert data["status"] == "ok"

    def test_version_endpoint(self, api_url):
        resp = urllib.request.urlopen(f"{api_url}/version", timeout=5)
        assert resp.status == 200
        data = json.loads(resp.read())
        # Version should be present (may be "0.0.0" in dev/editable mode)
        assert "version" in data or "current" in data
