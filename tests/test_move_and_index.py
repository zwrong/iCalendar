from types import SimpleNamespace

import pytest

from mcp_ical.caldav_client import CalDAVManager
from mcp_ical.models import UpdateEventRequest

RAW = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:ABC
DTSTAMP:20261011T100000Z
DTSTART:20261014T180000Z
DTEND:20261014T183000Z
SUMMARY:Dentist
X-CUSTOM:keep-me
END:VEVENT
END:VCALENDAR
"""


def calendar(mocker, name, url):
    cal = mocker.Mock()
    cal.get_display_name.return_value = name
    cal.url = url
    return cal


@pytest.fixture
def setup(mocker):
    mocker.patch("mcp_ical.caldav_client.get_config")
    mocker.patch("mcp_ical.caldav_client.DAVClient")
    mocker.patch("mcp_ical.caldav_client.Event.from_caldav_event", side_effect=lambda e: e)
    m = CalDAVManager()
    work = calendar(mocker, "Work", "https://dav/work/")
    home = calendar(mocker, "Personal", "https://dav/home/")
    m.principal.calendars.return_value = [work, home]
    existing = mocker.Mock(id="ABC", url="https://dav/work/odd-name.ics", data=RAW)
    mocker.patch.object(m, "_locate", return_value=(existing, work))
    m.client.request.return_value = SimpleNamespace(status=201, reason="Created")
    moved = mocker.Mock(id="ABC", url="https://dav/home/odd-name.ics", data=RAW)
    home.event_by_url.return_value = moved
    return m, work, home, existing, moved


def test_pure_move_uses_webdav_move_and_keeps_data(setup):
    m, work, home, existing, moved = setup
    result = m.update_event("ABC", UpdateEventRequest(calendar_name="Personal"))

    m.client.request.assert_called_once_with(
        "https://dav/work/odd-name.ics", "MOVE", "", {"Destination": "https://dav/home/odd-name.ics", "Overwrite": "F"}
    )
    home.event_by_url.assert_called_once_with("https://dav/home/odd-name.ics")
    moved.save.assert_not_called()  # original text untouched
    existing.delete.assert_not_called()
    work.add_event.assert_not_called()
    home.add_event.assert_not_called()
    assert result is moved


def test_move_with_changes_saves_after_move(setup):
    m, _work, _home, _existing, moved = setup
    m.update_event("ABC", UpdateEventRequest(calendar_name="Personal", title="Nouveau"))
    assert "SUMMARY:Nouveau" in moved.data
    moved.save.assert_called_once()


@pytest.mark.parametrize("response", [SimpleNamespace(status=412, reason="Precondition Failed"), RuntimeError("net")])
def test_failed_move_keeps_original(setup, response):
    m, _work, home, existing, _moved = setup
    if isinstance(response, Exception):
        m.client.request.side_effect = response
    else:
        m.client.request.return_value = response
    with pytest.raises(ValueError, match="left untouched"):
        m.update_event("ABC", UpdateEventRequest(calendar_name="Personal"))
    existing.delete.assert_not_called()
    home.event_by_url.assert_not_called()


def test_move_updates_index(setup):
    m, *_ = setup
    m._index["ABC"] = ("https://dav/work/", "https://dav/work/odd-name.ics")
    m.update_event("ABC", UpdateEventRequest(calendar_name="Personal"))
    assert m._index["ABC"] == ("https://dav/home/", "https://dav/home/odd-name.ics")


@pytest.fixture
def real_locate(mocker):
    mocker.patch("mcp_ical.caldav_client.get_config")
    mocker.patch("mcp_ical.caldav_client.DAVClient")
    m = CalDAVManager()
    work = calendar(mocker, "Work", "https://dav/work/")
    home = calendar(mocker, "Personal", "https://dav/home/")
    m.principal.calendars.return_value = [work, home]
    return m, work, home


def test_locate_uses_index_without_scanning(real_locate, mocker):
    m, work, home = real_locate
    m._index["ABC"] = ("https://dav/home/", "https://dav/home/whatever.ics")
    obj = mocker.Mock(id="ABC")
    home.event_by_url.return_value = obj

    assert m._locate("ABC") == (obj, home)
    home.event_by_url.assert_called_once_with("https://dav/home/whatever.ics")
    work.events.assert_not_called()
    home.events.assert_not_called()


def test_locate_falls_back_to_uid_url_then_scan(real_locate, mocker):
    m, work, home = real_locate
    work.event_by_url.side_effect = Exception("404")
    home.event_by_url.side_effect = Exception("404")
    odd = mocker.Mock(id="ABC", url="https://dav/home/odd-name.ics")
    other = mocker.Mock(id="XYZ", url="https://dav/work/XYZ.ics")
    work.events.return_value = [other]
    home.events.return_value = [odd]

    assert m._locate("ABC") == (odd, home)
    # the scan indexed everything it saw
    assert m._index["XYZ"] == ("https://dav/work/", "https://dav/work/XYZ.ics")


def test_stale_index_entry_is_dropped(real_locate, mocker):
    m, work, home = real_locate
    m._index["ABC"] = ("https://dav/home/", "https://dav/home/gone.ics")
    home.event_by_url.side_effect = Exception("404")
    work.event_by_url.side_effect = Exception("404")
    work.events.return_value = []
    home.events.return_value = []

    assert m._locate("ABC") is None
    assert "ABC" not in m._index
