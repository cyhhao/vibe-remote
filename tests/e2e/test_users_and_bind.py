"""E2E tests: User management and bind code API endpoints."""

import json
import urllib.request
import urllib.error


def _get(url, timeout=5):
    resp = urllib.request.urlopen(url, timeout=timeout)
    return resp.status, json.loads(resp.read())


def _post(url, body=None, timeout=5):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=timeout)
    return resp.status, json.loads(resp.read())


def _delete(url, timeout=5):
    req = urllib.request.Request(url, method="DELETE")
    resp = urllib.request.urlopen(req, timeout=timeout)
    return resp.status, json.loads(resp.read())


def _expect_error(url, method="DELETE", timeout=5):
    """Send request and expect an HTTP error (4xx/5xx)."""
    req = urllib.request.Request(url, method=method)
    try:
        urllib.request.urlopen(req, timeout=timeout)
        return None, None  # No error raised
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class TestBindCodeAPI:
    """Bind code CRUD via /api/bind-codes endpoint."""

    def test_get_bind_codes_empty(self, api_url):
        """GET /api/bind-codes should return empty list initially."""
        status, data = _get(f"{api_url}/api/bind-codes")
        assert status == 200
        assert data.get("ok") is True
        assert isinstance(data.get("bind_codes"), list)

    def test_create_one_time_bind_code(self, api_url):
        """POST /api/bind-codes should create a one-time code."""
        status, data = _post(f"{api_url}/api/bind-codes", {"type": "one_time"})
        assert status == 200
        assert data.get("ok") is True
        assert "bind_code" in data
        assert "code" in data["bind_code"]

    def test_create_and_list_bind_codes(self, api_url):
        """Created bind codes should appear in GET listing."""
        # Create a code
        _, create_data = _post(f"{api_url}/api/bind-codes", {"type": "one_time"})
        code = create_data["bind_code"]["code"]

        # List codes
        _, list_data = _get(f"{api_url}/api/bind-codes")
        codes = [bc["code"] for bc in list_data.get("bind_codes", [])]
        assert code in codes

    def test_delete_bind_code(self, api_url):
        """DELETE /api/bind-codes/<code> should deactivate the code."""
        # Create a code
        _, create_data = _post(f"{api_url}/api/bind-codes", {"type": "one_time"})
        code = create_data["bind_code"]["code"]

        # Delete it
        status, data = _delete(f"{api_url}/api/bind-codes/{code}")
        assert status == 200
        assert data.get("ok") is True

    def test_delete_nonexistent_code_returns_404(self, api_url):
        """DELETE /api/bind-codes/<nonexistent> should return 404."""
        error_code, data = _expect_error(f"{api_url}/api/bind-codes/NONEXISTENT_CODE_XYZ")
        assert error_code == 404

    def test_create_expiring_bind_code(self, api_url):
        """POST /api/bind-codes with type=expiring should accept expires_at."""
        status, data = _post(
            f"{api_url}/api/bind-codes",
            {"type": "expiring", "expires_at": "2099-12-31"},
        )
        assert status == 200
        assert data.get("ok") is True


class TestFirstBindCodeAPI:
    """Setup wizard first bind code endpoint."""

    def test_first_bind_code(self, api_url):
        """GET /api/setup/first-bind-code should return a code."""
        status, data = _get(f"{api_url}/api/setup/first-bind-code")
        assert status == 200
        assert data.get("ok") is True
        assert "code" in data


class TestUsersAPI:
    """User management via /api/users endpoint."""

    def test_get_users_returns_dict(self, api_url):
        """GET /api/users should return a users dict."""
        status, data = _get(f"{api_url}/api/users")
        assert status == 200
        assert data.get("ok") is True
        assert isinstance(data.get("users"), dict)

    def test_save_users_creates_user(self, api_url):
        """POST /api/users should create/update users."""
        payload = {
            "users": {
                "U_E2E_TEST": {
                    "display_name": "E2E Test User",
                    "is_admin": True,
                    "bound_at": "2025-01-01T00:00:00Z",
                    "enabled": True,
                }
            }
        }
        status, data = _post(f"{api_url}/api/users", payload)
        assert status == 200
        assert data.get("ok") is True

        # Verify the user persisted
        _, users_data = _get(f"{api_url}/api/users")
        assert "U_E2E_TEST" in users_data.get("users", {})
        assert users_data["users"]["U_E2E_TEST"]["display_name"] == "E2E Test User"

    def test_toggle_admin(self, api_url):
        """POST /api/users/<id>/admin should toggle admin status."""
        # First ensure user exists (as admin)
        _post(
            f"{api_url}/api/users",
            {
                "users": {
                    "U_ADMIN_TEST": {
                        "display_name": "Admin Test",
                        "is_admin": True,
                        "bound_at": "2025-01-01T00:00:00Z",
                        "enabled": True,
                    },
                    "U_NONADMIN_TEST": {
                        "display_name": "Non-Admin Test",
                        "is_admin": False,
                        "bound_at": "2025-01-01T00:00:00Z",
                        "enabled": True,
                    },
                }
            },
        )

        # Toggle non-admin to admin
        status, data = _post(
            f"{api_url}/api/users/U_NONADMIN_TEST/admin",
            {"is_admin": True},
        )
        assert status == 200
        assert data.get("ok") is True

    def test_delete_user(self, api_url):
        """DELETE /api/users/<id> should remove the user."""
        # Create a user to delete (ensure at least one admin remains)
        _post(
            f"{api_url}/api/users",
            {
                "users": {
                    "U_KEEP_ADMIN": {
                        "display_name": "Keep Admin",
                        "is_admin": True,
                        "bound_at": "2025-01-01T00:00:00Z",
                        "enabled": True,
                    },
                    "U_TO_DELETE": {
                        "display_name": "To Delete",
                        "is_admin": False,
                        "bound_at": "2025-01-01T00:00:00Z",
                        "enabled": True,
                    },
                }
            },
        )

        # Delete the user
        status, data = _delete(f"{api_url}/api/users/U_TO_DELETE")
        assert status == 200
        assert data.get("ok") is True

        # Verify deleted
        _, users_data = _get(f"{api_url}/api/users")
        assert "U_TO_DELETE" not in users_data.get("users", {})


class TestAgentModelsAPI:
    """Agent model listing endpoints (may return errors without CLIs installed)."""

    def test_claude_agents_endpoint_exists(self, api_url):
        """GET /claude/agents should return JSON (ok or error)."""
        status, data = _get(f"{api_url}/claude/agents")
        assert status == 200
        # May be {"ok": true, "agents": [...]} or {"ok": false, "error": "..."}
        assert isinstance(data, dict)

    def test_claude_models_endpoint_exists(self, api_url):
        """GET /claude/models should return JSON."""
        status, data = _get(f"{api_url}/claude/models")
        assert status == 200
        assert isinstance(data, dict)

    def test_codex_models_endpoint_exists(self, api_url):
        """GET /codex/models should return JSON."""
        status, data = _get(f"{api_url}/codex/models")
        assert status == 200
        assert isinstance(data, dict)
