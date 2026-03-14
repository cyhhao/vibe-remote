from types import SimpleNamespace

from core.auth import check_auth


class _Store:
    def __init__(self):
        self.reload_calls = 0
        self.settings = SimpleNamespace(channels={})
        self._bound_users = set()
        self._admins = set()

    def maybe_reload(self):
        self.reload_calls += 1

    def is_bound_user(self, user_id: str) -> bool:
        return user_id in self._bound_users

    def has_any_admin(self) -> bool:
        return bool(self._admins)

    def is_admin(self, user_id: str) -> bool:
        return user_id in self._admins


class _SettingsManager:
    def __init__(self, store):
        self._store = store

    def get_store(self):
        return self._store


def test_check_auth_uses_settings_manager_store():
    store = _Store()
    manager = _SettingsManager(store)

    result = check_auth(
        user_id="U1",
        channel_id="D1",
        is_dm=True,
        action="bind",
        settings_manager=manager,
    )

    assert result.allowed is True
    assert store.reload_calls == 1


def test_dm_bind_gate_denies_unbound_user():
    store = _Store()
    result = check_auth(
        user_id="U2",
        channel_id="D2",
        is_dm=True,
        action="settings",
        store=store,
    )

    assert result.allowed is False
    assert result.denial == "unbound_dm"


def test_dm_bind_command_is_exempt():
    store = _Store()
    result = check_auth(
        user_id="U3",
        channel_id="D3",
        is_dm=True,
        action="bind",
        store=store,
    )

    assert result.allowed is True


def test_channel_auth_denies_unconfigured_channel():
    store = _Store()
    result = check_auth(
        user_id="U4",
        channel_id="C-missing",
        is_dm=False,
        action="",
        store=store,
    )

    assert result.allowed is False
    assert result.denial == "unauthorized_channel"


def test_admin_guard_denies_non_admin_for_protected_action():
    store = _Store()
    store.settings.channels["C1"] = SimpleNamespace(enabled=True)
    store._bound_users.add("U5")
    store._admins.add("U-admin")

    result = check_auth(
        user_id="U5",
        channel_id="C1",
        is_dm=False,
        action="cmd_settings",
        store=store,
    )

    assert result.allowed is False
    assert result.denial == "not_admin"
