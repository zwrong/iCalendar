import json

import pytest
from loguru import logger

from mcp_ical.config import DEFAULT_SERVER_URL, ConfigManager

SECRET = "abcd-efgh-ijkl-mnop"


@pytest.fixture
def manager(tmp_path):
    """ConfigManager with an empty environment and a project root pointing to tmp_path."""

    def _make(environ=None):
        m = ConfigManager(environ=environ or {})
        m.private_config_path = tmp_path / "config_private.json"
        m.default_config_path = tmp_path / "config.json"
        return m

    return _make


def write_json(path, username, password="file-pass", server_url="https://file.example/"):
    path.write_text(json.dumps({"caldav": {"server_url": server_url, "username": username, "password": password}}))


def test_env_takes_priority_over_files(manager, tmp_path):
    write_json(tmp_path / "config_private.json", "file-user")
    explicit = tmp_path / "explicit.json"
    write_json(explicit, "explicit-user")

    cfg = manager(
        {"ICLOUD_USERNAME": "env-user", "ICLOUD_PASSWORD": SECRET, "MCP_ICAL_CONFIG": str(explicit)}
    ).load_config()

    assert cfg.caldav.username == "env-user"
    assert cfg.caldav.password == SECRET
    assert cfg.caldav.server_url == DEFAULT_SERVER_URL


def test_env_server_url_override(manager):
    cfg = manager(
        {"ICLOUD_USERNAME": "u", "ICLOUD_PASSWORD": "p", "CALDAV_SERVER_URL": "https://dav.example/"}
    ).load_config()
    assert cfg.caldav.server_url == "https://dav.example/"


@pytest.mark.parametrize("present", ["ICLOUD_USERNAME", "ICLOUD_PASSWORD"])
def test_incomplete_env_names_missing_variable(manager, present):
    with pytest.raises(ValueError) as exc:
        manager({present: "x"}).load_config()
    missing = "ICLOUD_PASSWORD" if present == "ICLOUD_USERNAME" else "ICLOUD_USERNAME"
    assert f"{missing} is not set" in str(exc.value)


def test_mcp_ical_config_beats_legacy_files(manager, tmp_path):
    write_json(tmp_path / "config_private.json", "legacy-user")
    explicit = tmp_path / "explicit.json"
    write_json(explicit, "explicit-user")

    cfg = manager({"MCP_ICAL_CONFIG": str(explicit)}).load_config()
    assert cfg.caldav.username == "explicit-user"


def test_mcp_ical_config_missing_file(manager, tmp_path):
    with pytest.raises(FileNotFoundError, match="MCP_ICAL_CONFIG"):
        manager({"MCP_ICAL_CONFIG": str(tmp_path / "nope.json")}).load_config()


def test_legacy_private_before_default(manager, tmp_path):
    write_json(tmp_path / "config.json", "default-user")
    write_json(tmp_path / "config_private.json", "private-user")
    assert manager().load_config().caldav.username == "private-user"
    (tmp_path / "config_private.json").unlink()
    assert manager().load_config().caldav.username == "default-user"


def test_no_config_error_lists_env_vars(manager):
    with pytest.raises(FileNotFoundError) as exc:
        manager().load_config()
    for name in ("ICLOUD_USERNAME", "ICLOUD_PASSWORD", "MCP_ICAL_CONFIG"):
        assert name in str(exc.value)


def test_password_never_logged_or_in_repr(manager, tmp_path):
    messages = []
    sink = logger.add(lambda m: messages.append(str(m)), level="TRACE")
    try:
        cfg = manager({"ICLOUD_USERNAME": "u", "ICLOUD_PASSWORD": SECRET}).load_config()
        write_json(tmp_path / "config_private.json", "u", password=SECRET)
        manager().load_config()
    finally:
        logger.remove(sink)

    assert messages, "expected at least one debug message"
    assert not any(SECRET in m for m in messages)
    assert SECRET not in repr(cfg)
