"""Business API for V2 config + settings store.

C3 of the services-layer refactor (Plan 1 in
``docs/plans/workbench-dispatch-architecture.md`` §6.4). Two formerly-
duplicated entry points collapse into one named seam:

* CLI's ``vibe.cli._ensure_config`` — creates a default config on first
  use, then loads via ``V2Config.load(paths.get_config_path())``.
* UI server's bare ``V2Config.load()`` — relies on ``V2Config.load()``'s
  default-path lookup; doesn't seed a default on first run.

Same for ``SettingsStore``: CLI passes ``paths.get_settings_path()``
explicitly, UI server calls ``SettingsStore.get_instance()`` with no
args. Behavior overlaps but the entry shape diverges, so this module
makes both go through one function.

Both helpers are thin and side-effect free: they don't mutate process
state beyond what the underlying primitives already do (V2Config has no
caching; SettingsStore owns its own thread-safe singleton). Callers must
keep being explicit about reloads.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from config import SettingsStore, paths
from config.v2_config import V2Config

DefaultConfigFactory = Callable[[], V2Config]


def load_config(
    config_path: Optional[Path] = None,
    *,
    default_factory: Optional[DefaultConfigFactory] = None,
) -> V2Config:
    """Load the V2 config, optionally seeding a default file when missing.

    Behavior depends on ``default_factory``:

    * **None** (default, matches the UI server's bare ``V2Config.load()``
      behavior today): the file must exist on disk. Raises
      ``FileNotFoundError`` otherwise — this is what the UI server
      prefers for boot-time guards.
    * **Callable** (matches the CLI's ``_ensure_config`` behavior): if
      the file does not exist, ``default_factory()`` is invoked and the
      result is persisted via ``V2Config.save`` before the regular load
      proceeds. The CLI passes its own minimal-default factory here.

    Routing both callers through this entry point keeps the seeding
    contract centrally documented and prevents the two paths from
    drifting (e.g. one auto-creating a file the other expects to be
    absent).
    """

    target = config_path or paths.get_config_path()
    if not target.exists():
        if default_factory is None:
            raise FileNotFoundError(f"V2 config not found at {target}")
        default = default_factory()
        default.save(target)
    return V2Config.load(target)


def get_settings_store(settings_path: Optional[Path] = None) -> SettingsStore:
    """Return the process-wide ``SettingsStore`` singleton.

    Wraps ``SettingsStore.get_instance`` so callers don't need to know
    that the store is a singleton or how it picks the default path.
    Reload-from-disk happens inside ``get_instance``; we expose
    ``reload_settings_store`` for the rare case where a caller wants to
    force it.
    """

    return SettingsStore.get_instance(settings_path)


def reload_settings_store(settings_path: Optional[Path] = None) -> SettingsStore:
    """Force the settings store to re-read from disk.

    ``get_settings_store`` already calls ``maybe_reload`` on each access
    but only when the singleton already exists; the explicit reload here
    is for callers (mostly tests, post-write inspection) that want to
    guarantee they see the freshly-persisted state.
    """

    store = SettingsStore.get_instance(settings_path)
    store.maybe_reload()
    return store


def reset_settings_store() -> None:
    """Test-only: tear down the singleton.

    Re-exports ``SettingsStore.reset_instance`` under the services
    namespace so tests don't have to reach into ``config.v2_settings``
    directly.
    """

    SettingsStore.reset_instance()


__all__ = [
    "load_config",
    "get_settings_store",
    "reload_settings_store",
    "reset_settings_store",
]
