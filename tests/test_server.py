import asyncio
import io
import logging

import pytest
from loguru import logger

from mcp_ical import server

WRITE_TOOL_NAMES = {fn.__name__ for fn in server.WRITE_TOOLS}
READ_TOOL_NAMES = {fn.__name__ for fn in server.READ_TOOLS}


def tool_names(srv):
    return {t.name for t in asyncio.run(srv.list_tools())}


def test_read_write_mode_registers_all_tools():
    names = tool_names(server.create_server(read_only=False))
    assert READ_TOOL_NAMES | WRITE_TOOL_NAMES <= names


def test_read_only_mode_does_not_register_write_tools():
    names = tool_names(server.create_server(read_only=True))
    assert READ_TOOL_NAMES <= names
    assert not names & WRITE_TOOL_NAMES


@pytest.mark.parametrize(
    "value,expected",
    [("true", True), ("TRUE", True), ("1", True), ("yes", True), ("false", False), ("0", False), ("", False)],
)
def test_is_read_only(value, expected):
    assert server.is_read_only({"MCP_ICAL_READ_ONLY": value}) is expected


def test_is_read_only_unset():
    assert server.is_read_only({}) is False


def test_read_only_tool_works_with_mocked_davclient(mocker, monkeypatch):
    """End to end through the MCP layer, with DAVClient mocked: no network."""
    monkeypatch.setenv("ICLOUD_USERNAME", "u")
    monkeypatch.setenv("ICLOUD_PASSWORD", "p")
    cal = mocker.Mock()
    cal.get_display_name.return_value = "Personal"
    client = mocker.patch("mcp_ical.caldav_client.DAVClient")
    client.return_value.principal.return_value.calendars.return_value = [cal]
    server.get_calendar_manager.cache_clear()

    srv = server.create_server(read_only=True)
    result = asyncio.run(srv.call_tool("list_calendars", {}))

    assert "Personal" in str(result)
    assert client.call_args.kwargs["url"] == "https://caldav.icloud.com/"
    assert client.call_args.kwargs["enable_rfc6764"] is False
    server.get_calendar_manager.cache_clear()


def capture_logs(level_env, monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(server.sys, "stderr", buf)
    if level_env is None:
        monkeypatch.delenv("MCP_ICAL_LOG_LEVEL", raising=False)
    else:
        monkeypatch.setenv("MCP_ICAL_LOG_LEVEL", level_env)
    server.configure_logging()
    logger.debug("debug-line")
    logger.info("info-line")
    return buf.getvalue()


def test_log_level_defaults_to_info(monkeypatch):
    out = capture_logs(None, monkeypatch)
    assert "info-line" in out and "debug-line" not in out


def test_log_level_from_env(monkeypatch):
    out = capture_logs("debug", monkeypatch)
    assert "debug-line" in out


def test_invalid_log_level_falls_back_to_info(monkeypatch):
    out = capture_logs("chatty", monkeypatch)
    assert "info-line" in out and "debug-line" not in out


@pytest.mark.parametrize("level_env,expected", [(None, logging.ERROR), ("INFO", logging.ERROR), ("DEBUG", logging.DEBUG)])
def test_caldav_warnings_only_when_debugging(monkeypatch, level_env, expected):
    capture_logs(level_env, monkeypatch)
    assert logging.getLogger("caldav").level == expected
