"""Read-only integration tests against the real CalDAV server.

Skipped unless ICLOUD_USERNAME is set. Nothing is created, modified or deleted.
"""

import os
from datetime import datetime, timedelta

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("ICLOUD_USERNAME"), reason="ICLOUD_USERNAME not set"),
]


@pytest.fixture(scope="module")
def manager():
    from mcp_ical.ical import CalendarManager

    return CalendarManager()


def test_list_calendars(manager):
    assert isinstance(manager.list_calendar_names(), list)


def test_list_events_last_week(manager):
    end = datetime.now()
    events = manager.list_events(end - timedelta(days=7), end)
    assert isinstance(events, list)
