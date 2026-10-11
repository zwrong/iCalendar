from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import vobject

from mcp_ical.caldav_client import CalDAVManager, event_dates
from mcp_ical.models import CreateEventRequest, RecurrenceRule, UpdateEventRequest

PARIS = timezone(timedelta(hours=2))


def test_fixed_offset_converted_to_utc():
    start, end = event_dates(
        datetime(2026, 10, 14, 20, 0, tzinfo=PARIS), datetime(2026, 10, 14, 20, 30, tzinfo=PARIS), False
    )
    assert start.utcoffset() == timedelta(0)
    assert (start.hour, end.minute) == (18, 30)


def test_all_day_uses_dates_with_exclusive_end():
    start, end = event_dates(datetime(2026, 10, 14, 0, 0), datetime(2026, 10, 14, 0, 0), True)
    assert (start, end) == (date(2026, 10, 14), date(2026, 10, 15))
    assert not isinstance(start, datetime)


@pytest.fixture
def manager(mocker):
    """CalDAVManager with no network: the target calendar records what add_event receives."""
    mocker.patch("mcp_ical.caldav_client.get_config")
    mocker.patch("mcp_ical.caldav_client.DAVClient")
    # add_event returns the saved object; here it just carries the vcal that would have been sent
    mocker.patch("mcp_ical.caldav_client.Event.from_caldav_event", side_effect=lambda e: e.vcal)
    m = CalDAVManager()
    cal = mocker.Mock()
    cal.get_display_name.return_value = "Personal"
    cal.add_event.side_effect = lambda vcal: SimpleNamespace(id="saved", vcal=vcal)
    mocker.patch.object(m, "_get_all_calendars", return_value=[cal])
    return m


def serialized(vcal):
    return vcal.serialize()


def test_create_event_serializes_offset_datetimes(manager):
    vcal = manager.create_event(
        CreateEventRequest(
            title="t",
            start_time=datetime(2026, 10, 14, 20, 0, tzinfo=PARIS),
            end_time=datetime(2026, 10, 14, 20, 30, tzinfo=PARIS),
            alarms_minutes_offsets=[15],
            recurrence_rule=RecurrenceRule(frequency=1, end_date=datetime(2026, 12, 1, 20, 0, tzinfo=PARIS)),
        )
    )
    out = serialized(vcal)
    assert "DTSTART:20261014T180000Z" in out
    assert "DTEND:20261014T183000Z" in out
    assert "UNTIL=20261201T180000Z" in out
    assert "TRIGGER:-PT15M" in out
    uid = vobject.readOne(out).vevent.uid.value
    assert " " not in uid and len(uid) == 36


def test_create_all_day_event(manager):
    vcal = manager.create_event(
        CreateEventRequest(title="t", start_time=datetime(2026, 10, 14), end_time=datetime(2026, 10, 14), all_day=True)
    )
    out = serialized(vcal)
    assert "DTSTART;VALUE=DATE:20261014" in out
    assert "DTEND;VALUE=DATE:20261015" in out


EXISTING = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:abc
DTSTAMP:20261011T100000Z
DTSTART;TZID=Europe/Paris:20261014T200000
DTEND;TZID=Europe/Paris:20261014T203000
SUMMARY:old
END:VEVENT
END:VCALENDAR
"""


def test_update_adds_missing_fields_and_moves_time(manager, mocker):
    existing = mocker.Mock()
    existing.data = EXISTING
    mocker.patch.object(manager, "_locate", return_value=(existing, manager._get_all_calendars()[0]))

    vcal = manager.update_event(
        "abc",
        UpdateEventRequest(
            title="new",
            location="Office",
            notes="n",
            start_time=datetime(2026, 10, 14, 21, 0, tzinfo=PARIS),
            end_time=datetime(2026, 10, 14, 22, 0, tzinfo=PARIS),
        ),
    )
    out = serialized(vcal)
    for expected in ("SUMMARY:new", "LOCATION:Office", "DESCRIPTION:n", "DTSTART:20261014T190000Z", "DTEND:20261014T200000Z"):
        assert expected in out


def test_update_to_all_day_keeps_existing_dates(manager, mocker):
    existing = mocker.Mock()
    existing.data = EXISTING
    mocker.patch.object(manager, "_locate", return_value=(existing, manager._get_all_calendars()[0]))

    out = serialized(manager.update_event("abc", UpdateEventRequest(all_day=True)))
    assert "DTSTART;VALUE=DATE:20261014" in out
    assert "DTEND;VALUE=DATE:20261015" in out
    assert vobject.readOne(out).vevent.dtstart.value == date(2026, 10, 14)
