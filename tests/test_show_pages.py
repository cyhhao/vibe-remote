import json

from config import paths
from config.v2_config import AgentsConfig, PlatformsConfig, RemoteAccessConfig, RuntimeConfig, SlackConfig, UiConfig, V2Config
from core.show_pages import ShowPageError, ShowPageStore, ensure_show_page_dir, show_page_payload
from storage.pagination import PageRequest
from vibe import cli


def test_show_without_subcommand_prints_help(capsys):
    parser = cli.build_parser()
    args = parser.parse_args(["show"])

    assert args.command == "show"
    assert args.show_command is None

    assert cli.cmd_show(args) == 0
    captured = capsys.readouterr()
    assert "Manage the one visual Show Page attached to an Agent Session." in captured.out
    assert "usage: vibe show [-h] {list,path,status,update} ..." in captured.out
    assert "vibe show list" in captured.out
    assert "vibe show path --session-id sesk8m4q2p7x" in captured.out


def _save_config() -> V2Config:
    config = V2Config(
        mode="self_host",
        version="v2",
        platform="slack",
        platforms=PlatformsConfig(enabled=["slack"], primary="slack"),
        slack=SlackConfig(bot_token=""),
        runtime=RuntimeConfig(default_cwd="."),
        agents=AgentsConfig(),
        ui=UiConfig(),
        remote_access=RemoteAccessConfig(),
    )
    cloud = config.remote_access.vibe_cloud
    cloud.enabled = True
    cloud.public_url = "https://alex.avibe.bot"
    config.save()
    return config


def test_store_defaults_to_private_and_rotates_public_share(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()
    _save_config()

    store = ShowPageStore()
    try:
        page = store.ensure("ses123")
        assert page.visibility == "private"
        assert page.share_id is None

        public_page = store.update_visibility("ses123", "public")
        assert public_page.visibility == "public"
        assert public_page.share_id

        rotated, old_share_id = store.rotate_share("ses123")
        assert old_share_id == public_page.share_id
        assert rotated.share_id != old_share_id
        assert store.get_by_share_id(old_share_id) is None
        assert store.get_by_share_id(rotated.share_id).session_id == "ses123"

        private_page = store.update_visibility("ses123", "private")
        assert private_page.visibility == "private"
        assert private_page.share_id == rotated.share_id

        offline_page = store.update_visibility("ses123", "offline")
        assert offline_page.offline
        assert offline_page.offline_at is not None
    finally:
        store.close()


def test_rotate_share_requires_public(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()

    store = ShowPageStore()
    try:
        store.ensure("ses123")
        try:
            store.rotate_share("ses123")
        except ShowPageError as exc:
            assert exc.code == "not_public"
        else:
            raise AssertionError("rotate_share should fail while private")
    finally:
        store.close()


def test_store_lists_pages_by_updated_time_and_visibility(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()
    _save_config()

    store = ShowPageStore()
    try:
        store.ensure("ses-old")
        store.ensure("ses-public")
        store.update_visibility("ses-public", "public")
        store.ensure("ses-offline")
        store.update_visibility("ses-offline", "offline")

        pages = store.list()
        assert [page.session_id for page in pages] == ["ses-offline", "ses-public", "ses-old"]

        public_pages = store.list(visibility="public")
        assert [page.session_id for page in public_pages] == ["ses-public"]
    finally:
        store.close()


def test_store_lists_show_pages_with_page_and_query(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()
    _save_config()

    store = ShowPageStore()
    try:
        for index in range(25):
            store.ensure(f"ses-{index:02d}")

        first_page = store.list_page(page_request=PageRequest(page=1, limit=20))
        second_page = store.list_page(page_request=PageRequest(page=2, limit=20))
        filtered = store.list_page(session_id="ses-2", query="ses-24", page_request=PageRequest(page=1, limit=20))

        assert first_page.has_more is True
        assert len(first_page.items) == 20
        assert second_page.has_more is False
        assert len(second_page.items) == 5
        assert [page.session_id for page in filtered.items] == ["ses-24"]
    finally:
        store.close()


def test_show_page_dir_creates_default_index(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    page_dir = ensure_show_page_dir("ses123")

    index_path = page_dir / "index.html"
    assert page_dir == tmp_path / "show" / "ses123"
    assert index_path.exists()
    assert "Ready to visualize" in index_path.read_text(encoding="utf-8")


def test_show_path_cli_json_creates_page(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()
    _save_config()

    args = cli.build_parser().parse_args(["show", "path", "--session-id", "ses123", "--json"])
    assert cli.cmd_show_path(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["visibility"] == "private"
    assert payload["active_url"] == "https://alex.avibe.bot/show/ses123/"
    assert payload["private_url"] == "https://alex.avibe.bot/show/ses123/"
    assert payload["public_url"] is None
    assert payload["url_available"] is True
    assert payload["url_guidance"] is None
    assert "Do not send implementation details such as local paths to the user unless they ask for them." in payload["next_actions"]
    assert "Treat the Show Page as the primary collaboration surface; put meaningful updates there first." in payload["next_actions"]
    assert (
        "Use visual thinking: diagrams, timelines, maps, comparisons, dashboards, or small prototypes when they help."
        in payload["next_actions"]
    )
    assert (tmp_path / "show" / "ses123" / "index.html").exists()


def test_show_list_cli_json_reports_existing_pages(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()
    _save_config()

    store = ShowPageStore()
    try:
        store.ensure("ses-private")
        store.update_visibility("ses-public", "public")
    finally:
        store.close()

    args = cli.build_parser().parse_args(["show", "list", "--json"])
    assert cli.cmd_show_list(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["count"] == 2
    assert [page["session_id"] for page in payload["pages"]] == ["ses-public", "ses-private"]
    public_page = payload["pages"][0]
    assert public_page["visibility"] == "public"
    assert public_page["active_url"] == public_page["public_url"]
    assert public_page["active_url"].startswith("https://alex.avibe.bot/p/")


def test_show_list_cli_json_reports_pagination(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()
    _save_config()

    store = ShowPageStore()
    try:
        for index in range(25):
            store.ensure(f"ses-page-{index:02d}")
    finally:
        store.close()

    args = cli.build_parser().parse_args(["show", "list", "--json"])
    assert cli.cmd_show_list(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 20
    assert payload["pagination"]["has_more"] is True
    assert payload["pagination"]["next_page"] == 2
    assert "vibe show list --json --page 2 --limit 20" == payload["pagination"]["next_command"]
    assert "还有更多记录" in payload["message"]


def test_show_list_cli_filters_visibility(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()
    _save_config()

    store = ShowPageStore()
    try:
        store.ensure("ses-private")
        store.update_visibility("ses-public", "public")
    finally:
        store.close()

    args = cli.build_parser().parse_args(["show", "list", "--visibility", "private"])
    assert cli.cmd_show_list(args) == 0

    output = capsys.readouterr().out
    assert "Count: 1" in output
    assert "Filter: visibility=private" in output
    assert "- ses-private" in output
    assert "- ses-public" not in output


def test_show_page_payload_requires_enabled_avibe_cloud(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()
    config = _save_config()
    config.remote_access.vibe_cloud.enabled = False
    config.save()

    store = ShowPageStore()
    try:
        page = store.ensure("ses123")
        payload = show_page_payload(page)
        assert payload["active_url"] is None
        assert payload["private_url"] is None
        assert payload["public_url"] is None
        assert payload["url_available"] is False
        assert "Avibe Cloud is not connected" in payload["url_guidance"]
        assert "avibe.bot" in payload["url_guidance"]
        assert "`vibe remote pair`" in payload["url_guidance"]
    finally:
        store.close()


def test_show_update_cli_reports_transition_urls(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()
    _save_config()

    parser = cli.build_parser()
    assert cli.cmd_show_path(parser.parse_args(["show", "path", "--session-id", "ses123", "--json"])) == 0
    capsys.readouterr()

    args = parser.parse_args(["show", "update", "--session-id", "ses123", "--visibility", "public", "--json"])
    assert cli.cmd_show_update(args) == 0
    public_payload = json.loads(capsys.readouterr().out)
    assert public_payload["visibility"] == "public"
    assert public_payload["active_url"] == public_payload["public_url"]
    assert public_payload["public_url"].startswith("https://alex.avibe.bot/p/")
    assert public_payload["previous_private_url"] == "https://alex.avibe.bot/show/ses123/"

    args = parser.parse_args(["show", "update", "--session-id", "ses123", "--visibility", "private", "--json"])
    assert cli.cmd_show_update(args) == 0
    private_payload = json.loads(capsys.readouterr().out)
    assert private_payload["visibility"] == "private"
    assert private_payload["active_url"] == "https://alex.avibe.bot/show/ses123/"
    assert private_payload["previous_public_url"] == public_payload["public_url"]


def test_show_update_rotate_share_fails_while_private(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    paths.ensure_data_dirs()

    parser = cli.build_parser()
    args = parser.parse_args(["show", "update", "--session-id", "ses123", "--rotate-share", "--json"])
    assert cli.cmd_show_update(args) == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload["code"] == "not_public"
