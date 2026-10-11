import uuid
from datetime import date, datetime, timedelta
from urllib.parse import quote, unquote

import vobject
from caldav import Calendar, CalendarObjectResource, DAVClient
from dateutil import tz
from loguru import logger

from .config import get_config
from .models import (
    CreateEventRequest,
    Event,
    UpdateEventRequest,
)


def to_ical_datetime(value: datetime) -> datetime:
    """Return an aware UTC datetime that vobject can serialize.

    vobject cannot name a fixed-offset timezone such as +02:00 (nor datetime.timezone.utc), and a naive
    datetime would be written as floating time. Naive values are taken as the machine's local time.
    """
    return value.astimezone(tz.UTC)


def to_ical_date(value: date) -> date:
    return value.date() if isinstance(value, datetime) else value


def event_dates(start: date, end: date, all_day: bool) -> tuple[date, date]:
    """DTSTART/DTEND values: dates for all-day events (DTEND exclusive), UTC datetimes otherwise."""
    if all_day:
        start, end = to_ical_date(start), to_ical_date(end)
        if end <= start:
            end = start + timedelta(days=1)
        return start, end
    if not isinstance(start, datetime):
        start = datetime.combine(start, datetime.min.time())
    if not isinstance(end, datetime):
        end = datetime.combine(end, datetime.min.time())
    return to_ical_datetime(start), to_ical_datetime(end)


def set_text(vevent, name: str, value: str) -> None:
    """Set a text property, creating it when the event does not have it yet."""
    if name in vevent.contents:
        getattr(vevent, name).value = value
    else:
        vevent.add(name).value = value


class CalDAVManager:
    """CalDAV client for managing iCloud calendar events."""

    def __init__(self):
        self.config = get_config()
        self.client = None
        self.principal = None
        # In-memory index filled by every listing: event id (UID) -> (calendar URL, event URL).
        # The server process lives as long as the MCP session, so this avoids rescanning calendars.
        self._index: dict[str, tuple[str, str]] = {}
        self._calendars: list[Calendar] | None = None
        self._connect()

    def _connect(self):
        """Establish connection to CalDAV server."""
        try:
            self.client = DAVClient(
                url=self.config.caldav.server_url,
                username=self.config.caldav.username,
                password=self.config.caldav.password,
                # The URL is explicit: no DNS-based service discovery
                enable_rfc6764=False,
            )
            self.principal = self.client.principal()
            logger.info("Successfully connected to CalDAV server")
        except Exception as e:
            logger.error(f"Failed to connect to CalDAV server: {e}")
            raise ValueError(
                f"Failed to connect to CalDAV server. Please check your credentials. Error: {e}"
            )

    def list_events(
        self,
        start_time: datetime,
        end_time: datetime,
        calendar_name: str | None = None,
    ) -> list[Event]:
        """List all events within a given date range."""
        logger.debug(f"list_events called with start_time={start_time}, end_time={end_time}, calendar_name={calendar_name}")

        calendar = self._find_calendar_by_name(calendar_name) if calendar_name else None
        if calendar_name and not calendar:
            raise ValueError(f"Calendar '{calendar_name}' not found")

        calendars = [calendar] if calendar else self._get_all_calendars()
        logger.debug(f"Found {len(calendars)} calendar(s) to search")

        events = []
        for i, cal in enumerate(calendars):
            try:
                logger.debug(f"Searching calendar {i+1}/{len(calendars)}: {cal.get_display_name()}")
                logger.debug(f"Calendar URL: {cal.url}")

                # Use CalDAV time-range search
                logger.debug(f"Performing CalDAV search with start={start_time}, end={end_time}, expand=True")
                results = cal.search(
                    start=start_time,
                    end=end_time,
                    event=True,
                    expand=True
                )

                logger.debug(f"CalDAV search returned {len(results)} raw events from calendar '{cal.get_display_name()}'")

                for j, event in enumerate(results):
                    try:
                        logger.debug(f"Processing event {j+1}/{len(results)} from calendar '{cal.get_display_name()}'")
                        logger.debug(f"Raw event ID: {event.id}, URL: {event.url}")

                        parsed_event = Event.from_caldav_event(event)
                        self._remember(event, cal)
                        logger.debug(f"Successfully parsed event: {parsed_event.title}")
                        events.append(parsed_event)

                    except Exception as parse_error:
                        logger.error(f"Failed to parse event {event.id} from calendar '{cal.get_display_name()}': {parse_error}")
                        logger.debug(f"Event data that failed to parse: {event.data}")
                        # Continue processing other events instead of failing completely
                        continue

            except Exception as e:
                logger.error(f"Failed to search events in calendar {cal.get_display_name()}: {e}")
                logger.debug(f"Calendar details that failed: name='{cal.get_display_name()}', url='{cal.url}'")
                # Continue with other calendars instead of failing completely
                continue

        events.sort(key=Event.sort_key)
        logger.debug(f"Returning total of {len(events)} successfully parsed events")
        return events

    def create_event(self, new_event: CreateEventRequest) -> Event:
        """Create a new calendar event."""
        calendar = self._find_calendar_by_name(new_event.calendar_name) if new_event.calendar_name else None
        if new_event.calendar_name and not calendar:
            raise ValueError(f"Calendar '{new_event.calendar_name}' not found")

        # Use default calendar if none specified
        if not calendar:
            calendars = self._get_all_calendars()
            if not calendars:
                raise ValueError("No calendars available")
            calendar = calendars[0]  # Use first available calendar

        # Create vCalendar event
        vcal = vobject.iCalendar()
        vevent = vcal.add('vevent')

        # Set basic properties
        # vobject's default UID contains spaces and the hostname; use a UUID like Apple clients do
        vevent.add('uid').value = str(uuid.uuid4()).upper()
        vevent.add('summary').value = new_event.title
        start, end = event_dates(new_event.start_time, new_event.end_time, new_event.all_day)
        vevent.add('dtstart').value = start
        vevent.add('dtend').value = end

        if new_event.notes:
            vevent.add('description').value = new_event.notes

        if new_event.location:
            vevent.add('location').value = new_event.location

        if new_event.url:
            vevent.add('url').value = new_event.url

        # Add alarms
        if new_event.alarms_minutes_offsets:
            for minutes in new_event.alarms_minutes_offsets:
                valarm = vevent.add('valarm')
                valarm.add('action').value = 'DISPLAY'
                valarm.add('description').value = new_event.title
                valarm.add('trigger').value = timedelta(minutes=-minutes)

        # Add recurrence rule
        if new_event.recurrence_rule:
            rrule_str = self._recurrence_rule_to_ical(new_event.recurrence_rule)
            vevent.add('rrule').value = rrule_str

        try:
            # Save event to calendar
            event = calendar.add_event(vcal)
            logger.info(f"Successfully created event: {event.id}")
            return Event.from_caldav_event(event)

        except Exception as e:
            logger.error(f"Failed to create event: {e}")
            raise

    def update_event(self, event_id: str, request: UpdateEventRequest) -> Event:
        """Update an existing event."""
        located = self._locate(event_id)
        if not located:
            raise ValueError(f"Event with ID '{event_id}' not found")
        existing_event, calendar = located

        # Load the event data
        event_data = existing_event.data
        # Handle both string and vobject data (similar to Event.from_caldav_event)
        if isinstance(event_data, str):
            # Parse string data into vobject
            vcal = vobject.readOne(event_data)
            logger.debug("Parsed string event data into vobject for update")
        else:
            vcal = event_data
            logger.debug("Using existing vobject event data for update")

        if not hasattr(vcal, 'vevent'):
            raise ValueError("Event data has no vevent component")

        vevent = vcal.vevent

        # Update fields
        if request.title is not None:
            vevent.summary.value = request.title
        if request.start_time is not None or request.end_time is not None or request.all_day is not None:
            current_start = vevent.dtstart.value
            current_end = vevent.dtend.value if 'dtend' in vevent.contents else current_start
            currently_all_day = not isinstance(current_start, datetime)
            start, end = event_dates(
                request.start_time if request.start_time is not None else current_start,
                request.end_time if request.end_time is not None else current_end,
                request.all_day if request.all_day is not None else currently_all_day,
            )
            vevent.dtstart.value = start
            if 'dtend' in vevent.contents:
                vevent.dtend.value = end
            else:
                vevent.add('dtend').value = end
            for prop in (vevent.dtstart, vevent.dtend):
                prop.params.pop('VALUE', None)
                prop.params.pop('TZID', None)
                prop.params.pop('X-VOBJ-ORIGINAL-TZID', None)
        if request.location is not None:
            set_text(vevent, 'location', request.location)
        if request.notes is not None:
            set_text(vevent, 'description', request.notes)
        if request.url is not None:
            set_text(vevent, 'url', request.url)

        # Update alarms
        if request.alarms_minutes_offsets is not None:
            # Remove existing alarms
            for alarm in list(vevent.getChildren()):
                if alarm.name == 'VALARM':
                    vevent.remove(alarm)

            # Add new alarms
            for minutes in request.alarms_minutes_offsets:
                valarm = vevent.add('valarm')
                valarm.add('action').value = 'DISPLAY'
                valarm.add('description').value = vevent.summary.value
                valarm.add('trigger').value = timedelta(minutes=-minutes)

        # Update recurrence rule
        if request.recurrence_rule is not None:
            if 'rrule' in vevent.contents:
                vevent.remove(vevent.rrule)

            if request.recurrence_rule:
                rrule_str = self._recurrence_rule_to_ical(request.recurrence_rule)
                vevent.add('rrule').value = rrule_str

        content_changed = any(
            v is not None
            for k, v in request.model_dump().items()
            if k != "calendar_name"
        )

        # Handle calendar change
        if request.calendar_name and request.calendar_name != calendar.get_display_name():
            new_calendar = self._find_calendar_by_name(request.calendar_name)
            if not new_calendar:
                raise ValueError(f"Calendar '{request.calendar_name}' not found")
            # A pure move keeps the original iCalendar text untouched
            return self._move(existing_event, calendar, new_calendar, vcal if content_changed else None)

        try:
            # Save updated event
            event = calendar.add_event(vcal)
            self._remember(event, calendar)
            logger.info(f"Successfully updated event: {event.id}")
            return Event.from_caldav_event(event)

        except Exception as e:
            logger.error(f"Failed to update event: {e}")
            raise

    def _move(self, existing_event, old_calendar: Calendar, new_calendar: Calendar, changed_vcal) -> Event:
        """Move an event with a WebDAV MOVE: atomic on the server, same UID, same file name.

        iCloud refuses the same UID in two calendars (412), so copy-then-delete is not possible. With MOVE
        there is no window where the event exists nowhere, and a failed MOVE leaves the original in place.
        """
        source = str(existing_event.url)
        target = str(new_calendar.url) + source.rstrip("/").rsplit("/", 1)[-1]
        try:
            response = self.client.request(source, "MOVE", "", {"Destination": target, "Overwrite": "F"})
            status = response.status
        except Exception as e:
            status, reason = None, str(e)
        else:
            reason = getattr(response, "reason", "")
        if status not in (201, 204):
            logger.error(f"MOVE failed ({status} {reason}), original left untouched")
            raise ValueError(
                f"Could not move the event to '{new_calendar.get_display_name()}' ({status} {reason}), "
                f"the original is left untouched in '{old_calendar.get_display_name()}'"
            )
        self._forget(existing_event)

        moved = new_calendar.event_by_url(target)
        if changed_vcal is not None:
            moved.data = changed_vcal.serialize()
            moved.save()
        moved.load()
        self._remember(moved, new_calendar)
        logger.info(f"Moved event {moved.id} to another calendar")
        return Event.from_caldav_event(moved)

    def delete_event(self, event_id: str) -> bool:
        """Delete an event by its identifier."""
        located = self._locate(event_id)
        if not located:
            raise ValueError(f"Event with ID '{event_id}' not found")
        existing_event, _ = located

        try:
            existing_event.delete()
            self._forget(existing_event)
            logger.info(f"Successfully deleted event with ID: {event_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to delete event: {e}")
            raise

    def find_event_by_id(self, event_id: str) -> CalendarObjectResource | None:
        """Find an event by its identifier (UID or URL)."""
        located = self._locate(event_id)
        return located[0] if located else None

    def _locate(self, event_id: str) -> tuple[CalendarObjectResource, Calendar] | None:
        """Find an event and its calendar.

        1. In-memory index (one GET on the known URL)
        2. iCloud stores most events at <calendar>/<UID>.ics (one GET per calendar)
        3. Full scan of every calendar, which refills the index
        """
        decoded = unquote(event_id)
        calendars = self._get_all_calendars()
        by_url = {str(c.url): c for c in calendars}

        cached = self._index.get(decoded)
        if cached and cached[0] in by_url:
            found = self._load(by_url[cached[0]], cached[1])
            if found:
                logger.debug(f"Found event {decoded} via the in-memory index")
                return found, by_url[cached[0]]
            self._index.pop(decoded, None)

        if "/" not in decoded:
            for calendar in calendars:
                found = self._load(calendar, str(calendar.url) + quote(decoded) + ".ics")
                if found and found.id in (event_id, decoded):
                    logger.debug(f"Found event {decoded} at its UID URL")
                    self._remember(found, calendar)
                    return found, calendar

        logger.debug(f"Scanning all calendars for event {decoded}")
        result = None
        for calendar in self._get_all_calendars(refresh=True):
            try:
                events = calendar.events()
            except Exception as e:
                logger.error(f"Failed to search calendar {calendar.get_display_name()} for event {event_id}: {e}")
                continue
            for event in events:
                self._remember(event, calendar)
                ids = {event.id, unquote(event.id)} if event.id else set()
                urls = {str(event.url), unquote(str(event.url))} if event.url else set()
                if result is None and (event_id in ids | urls or decoded in ids | urls):
                    result = (event, calendar)
        if result is None:
            logger.debug(f"Event with ID '{event_id}' not found in any calendar")
        return result

    @staticmethod
    def _load(calendar: Calendar, url: str) -> CalendarObjectResource | None:
        try:
            event = calendar.event_by_url(url)
            event.load()
            return event
        except Exception:
            return None

    def _remember(self, event, calendar: Calendar) -> None:
        if getattr(event, "id", None) and getattr(event, "url", None):
            self._index[event.id] = (str(calendar.url), str(event.url))

    def _forget(self, event) -> None:
        if getattr(event, "id", None):
            self._index.pop(event.id, None)

    def list_calendar_names(self) -> list[str]:
        """List all available calendar names."""
        calendars = self._get_all_calendars(refresh=True)
        return [cal.get_display_name() for cal in calendars]

    def _get_all_calendars(self, refresh: bool = False) -> list[Calendar]:
        """Get all calendars from the CalDAV server (cached; refresh=True forces a new PROPFIND)."""
        if self._calendars is not None and not refresh:
            return self._calendars
        try:
            calendars = self.principal.calendars()
            logger.debug(f"Successfully retrieved {len(calendars)} calendars")
            for cal in calendars:
                logger.debug(f"Calendar: name='{cal.get_display_name()}', url='{cal.url}'")
            self._calendars = calendars
            return calendars
        except Exception as e:
            logger.error(f"Failed to get calendars: {e}")
            return self._calendars or []

    def _find_calendar_by_name(self, calendar_name: str) -> Calendar | None:
        """Find a calendar by name, refreshing the cached list once if it is unknown."""
        for refresh in (False, True):
            for calendar in self._get_all_calendars(refresh=refresh):
                if calendar.get_display_name() == calendar_name:
                    return calendar
        return None

    def _recurrence_rule_to_ical(self, recurrence_rule) -> str:
        """Convert RecurrenceRule to iCalendar format."""
        frequency_map = {
            0: 'DAILY',
            1: 'WEEKLY',
            2: 'MONTHLY',
            3: 'YEARLY'
        }

        parts = [f'FREQ={frequency_map[recurrence_rule.frequency]}']

        if recurrence_rule.interval > 1:
            parts.append(f'INTERVAL={recurrence_rule.interval}')

        if recurrence_rule.days_of_week:
            weekday_map = {
                1: 'SU', 2: 'MO', 3: 'TU', 4: 'WE', 5: 'TH', 6: 'FR', 7: 'SA'
            }
            days = [weekday_map[day] for day in recurrence_rule.days_of_week]
            parts.append(f'BYDAY={",".join(days)}')

        if recurrence_rule.end_date:
            parts.append(f'UNTIL={to_ical_datetime(recurrence_rule.end_date).strftime("%Y%m%dT%H%M%SZ")}')
        elif recurrence_rule.occurrence_count:
            parts.append(f'COUNT={recurrence_rule.occurrence_count}')

        return ';'.join(parts)