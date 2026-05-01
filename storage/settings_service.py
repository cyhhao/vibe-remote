from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, select

from config.v2_settings import (
    BindCode,
    ChannelSettings,
    GuildSettings,
    RoutingSettings,
    SettingsState,
    UserSettings,
    _make_scoped_key,
    _split_scoped_key,
)
from storage.db import SqliteInvalidationProbe, create_sqlite_engine
from storage.models import (
    bind_codes,
    channel_settings,
    guild_policies,
    guild_settings,
    scopes,
    user_settings,
)


class SQLiteSettingsService:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.engine = create_sqlite_engine(db_path)
        self._probe = SqliteInvalidationProbe(self.engine)

    def close(self) -> None:
        self._probe.close()
        self.engine.dispose()

    def has_external_write(self) -> bool:
        return self._probe.has_external_write()

    def load_state(self) -> SettingsState:
        with self.engine.connect() as conn:
            return SettingsState(
                channels=self._load_channels(conn),
                guilds=self._load_guilds(conn),
                guild_scope_platforms=self._load_guild_scope_platforms(conn),
                guild_default_enabled=self._load_guild_policies(conn),
                users=self._load_users(conn),
                bind_codes=self._load_bind_codes(conn),
            )

    def save_state(self, state: SettingsState) -> None:
        with self.engine.begin() as conn:
            self._clear(conn)
            now = _utc_now_iso()
            for scoped_key, item in state.channels.items():
                platform, channel_id = _split_scoped_key(scoped_key)
                scope_id = self._get_or_create_scope(conn, platform or "unknown", "channel", channel_id, now=now)
                conn.execute(
                    channel_settings.insert().values(
                        scope_id=scope_id,
                        enabled=_bool_int(item.enabled),
                        show_message_types_json=_json_dumps(item.show_message_types),
                        custom_cwd=item.custom_cwd,
                        routing_json=_json_dumps(asdict(item.routing)),
                        require_mention=_nullable_bool_int(item.require_mention),
                        created_at=now,
                        updated_at=now,
                    )
                )

            for platform in sorted(state.guild_scope_platforms):
                conn.execute(
                    guild_policies.insert().values(
                        platform=platform,
                        default_enabled=_bool_int(state.guild_default_enabled.get(platform, False)),
                        created_at=now,
                        updated_at=now,
                    )
                )

            for scoped_key, item in state.guilds.items():
                platform, guild_id = _split_scoped_key(scoped_key)
                scope_id = self._get_or_create_scope(conn, platform or "discord", "guild", guild_id, now=now)
                conn.execute(
                    guild_settings.insert().values(
                        scope_id=scope_id,
                        enabled=_bool_int(item.enabled),
                        created_at=now,
                        updated_at=now,
                    )
                )

            for scoped_key, item in state.users.items():
                platform, user_id = _split_scoped_key(scoped_key)
                scope_id = self._get_or_create_scope(
                    conn,
                    platform or "unknown",
                    "user",
                    user_id,
                    display_name=item.display_name,
                    now=now,
                )
                conn.execute(
                    user_settings.insert().values(
                        scope_id=scope_id,
                        is_admin=_bool_int(item.is_admin),
                        bound_at=item.bound_at or None,
                        enabled=_bool_int(item.enabled),
                        show_message_types_json=_json_dumps(item.show_message_types),
                        custom_cwd=item.custom_cwd,
                        routing_json=_json_dumps(asdict(item.routing)),
                        dm_chat_id=item.dm_chat_id or None,
                        created_at=now,
                        updated_at=now,
                    )
                )

            for item in state.bind_codes:
                conn.execute(
                    bind_codes.insert().values(
                        code=item.code,
                        type=item.type,
                        created_at=item.created_at or now,
                        expires_at=item.expires_at,
                        is_active=_bool_int(item.is_active),
                        used_by_json=_json_dumps(item.used_by),
                    )
                )

    def _clear(self, conn: Connection) -> None:
        conn.execute(channel_settings.delete())
        conn.execute(guild_settings.delete())
        conn.execute(guild_policies.delete())
        conn.execute(user_settings.delete())
        conn.execute(bind_codes.delete())
        conn.execute(scopes.delete())

    def _load_channels(self, conn: Connection) -> dict[str, ChannelSettings]:
        rows = conn.execute(
            select(scopes, channel_settings).join(channel_settings, channel_settings.c.scope_id == scopes.c.id)
        ).mappings()
        result: dict[str, ChannelSettings] = {}
        for row in rows:
            key = _make_scoped_key(str(row["platform"]), str(row["scope_id"]))
            result[key] = ChannelSettings(
                enabled=bool(row["enabled"]),
                show_message_types=_json_loads(row["show_message_types_json"], []),
                custom_cwd=row["custom_cwd"],
                routing=_routing_from_json(row["routing_json"]),
                require_mention=_nullable_bool(row["require_mention"]),
            )
        return result

    def _load_guilds(self, conn: Connection) -> dict[str, GuildSettings]:
        rows = conn.execute(
            select(scopes, guild_settings).join(guild_settings, guild_settings.c.scope_id == scopes.c.id)
        ).mappings()
        result: dict[str, GuildSettings] = {}
        for row in rows:
            key = _make_scoped_key(str(row["platform"]), str(row["scope_id"]))
            result[key] = GuildSettings(enabled=bool(row["enabled"]))
        return result

    def _load_guild_scope_platforms(self, conn: Connection) -> set[str]:
        return {str(row[0]) for row in conn.execute(select(guild_policies.c.platform))}

    def _load_guild_policies(self, conn: Connection) -> dict[str, bool]:
        rows = conn.execute(select(guild_policies.c.platform, guild_policies.c.default_enabled))
        return {str(platform): bool(default_enabled) for platform, default_enabled in rows}

    def _load_users(self, conn: Connection) -> dict[str, UserSettings]:
        rows = conn.execute(
            select(scopes, user_settings).join(user_settings, user_settings.c.scope_id == scopes.c.id)
        ).mappings()
        result: dict[str, UserSettings] = {}
        for row in rows:
            key = _make_scoped_key(str(row["platform"]), str(row["scope_id"]))
            result[key] = UserSettings(
                display_name=row["display_name"] or "",
                is_admin=bool(row["is_admin"]),
                bound_at=row["bound_at"] or "",
                enabled=bool(row["enabled"]),
                show_message_types=_json_loads(row["show_message_types_json"], []),
                custom_cwd=row["custom_cwd"],
                routing=_routing_from_json(row["routing_json"]),
                dm_chat_id=row["dm_chat_id"] or "",
            )
        return result

    def _load_bind_codes(self, conn: Connection) -> list[BindCode]:
        rows = conn.execute(select(bind_codes)).mappings()
        return [
            BindCode(
                code=row["code"],
                type=row["type"],
                created_at=row["created_at"],
                expires_at=row["expires_at"],
                is_active=bool(row["is_active"]),
                used_by=_json_loads(row["used_by_json"], []),
            )
            for row in rows
        ]

    def _get_or_create_scope(
        self,
        conn: Connection,
        platform: str,
        scope_type: str,
        scope_id: str,
        *,
        now: str,
        display_name: str | None = None,
    ) -> int:
        existing = conn.execute(
            select(scopes.c.id).where(
                scopes.c.platform == platform,
                scopes.c.scope_type == scope_type,
                scopes.c.scope_id == scope_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return int(existing)
        result = conn.execute(
            scopes.insert().values(
                platform=platform,
                scope_type=scope_type,
                scope_id=scope_id,
                display_name=display_name,
                created_at=now,
                updated_at=now,
            )
        )
        return int(result.inserted_primary_key[0])


def _routing_from_json(value: str) -> RoutingSettings:
    payload = _json_loads(value, {})
    return RoutingSettings(
        agent_backend=payload.get("agent_backend"),
        opencode_agent=payload.get("opencode_agent"),
        opencode_model=payload.get("opencode_model"),
        opencode_reasoning_effort=payload.get("opencode_reasoning_effort"),
        claude_agent=payload.get("claude_agent"),
        claude_model=payload.get("claude_model"),
        claude_reasoning_effort=payload.get("claude_reasoning_effort"),
        codex_agent=payload.get("codex_agent"),
        codex_model=payload.get("codex_model"),
        codex_reasoning_effort=payload.get("codex_reasoning_effort"),
    )


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _bool_int(value: Any) -> int:
    return 1 if bool(value) else 0


def _nullable_bool(value: Any) -> bool | None:
    return None if value is None else bool(value)


def _nullable_bool_int(value: Any) -> int | None:
    return None if value is None else _bool_int(value)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
