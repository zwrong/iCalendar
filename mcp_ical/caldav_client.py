import sys
from datetime import datetime, timedelta
from typing import Any, List, Optional
from urllib.parse import urljoin, unquote

import vobject
from caldav import DAVClient, Calendar, CalendarObjectResource
from loguru import logger

from .config import get_config
from .models import (
    CreateEventRequest,
    Event,
    UpdateEventRequest,
)


class CalDAVManager:
    """CalDAV client for managing iCloud calendar events."""

    def __init__(self):
        self.config = get_config()
        self.client = None
        self.principal = None
        self._connect()

    def _connect(self):
        """Establish connection to CalDAV server."""
        try:
            self.client = DAVClient(
                url=self.config.caldav.server_url,
                username=self.config.caldav.username,
                password=self.config.caldav.password
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
        calendar_name: Optional[str] = None,
    ) -> List[Event]:
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
                logger.debug(f"Searching calendar {i+1}/{len(calendars)}: {cal.name}")
                logger.debug(f"Calendar URL: {cal.url}")

                # Use CalDAV time-range search
                logger.debug(f"Performing CalDAV search with start={start_time}, end={end_time}, expand=True")
                results = cal.search(
                    start=start_time,
                    end=end_time,
                    event=True,
                    expand=True
                )

                logger.debug(f"CalDAV search returned {len(results)} raw events from calendar '{cal.name}'")

                for j, event in enumerate(results):
                    try:
                        logger.debug(f"Processing event {j+1}/{len(results)} from calendar '{cal.name}'")
                        logger.debug(f"Raw event ID: {event.id}, URL: {event.url}")

                        parsed_event = Event.from_caldav_event(event)
                        logger.debug(f"Successfully parsed event: {parsed_event.title}")
                        events.append(parsed_event)

                    except Exception as parse_error:
                        logger.error(f"Failed to parse event {event.id} from calendar '{cal.name}': {parse_error}")
                        logger.debug(f"Event data that failed to parse: {event.data}")
                        # Continue processing other events instead of failing completely
                        continue

            except Exception as e:
                logger.error(f"Failed to search events in calendar {cal.name}: {e}")
                logger.debug(f"Calendar details that failed: name='{cal.name}', url='{cal.url}'")
                # Continue with other calendars instead of failing completely
                continue

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
        vevent.add('summary').value = new_event.title
        vevent.add('dtstart').value = new_event.start_time
        vevent.add('dtend').value = new_event.end_time

        if new_event.notes:
            vevent.add('description').value = new_event.notes

        if new_event.location:
            vevent.add('location').value = new_event.location

        if new_event.url:
            vevent.add('url').value = new_event.url

        # Handle all-day events
        if new_event.all_day:
            vevent.dtstart.value_param = 'DATE'
            vevent.dtend.value_param = 'DATE'

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
            event = calendar.save_event(vcal)
            logger.info(f"Successfully created event: {new_event.title}")
            return Event.from_caldav_event(event)

        except Exception as e:
            logger.error(f"Failed to create event: {e}")
            raise

    def update_event(self, event_id: str, request: UpdateEventRequest) -> Event:
        """Update an existing event."""
        existing_event = self.find_event_by_id(event_id)
        if not existing_event:
            raise ValueError(f"Event with ID '{event_id}' not found")

        # Get the calendar containing this event
        calendar = self._find_event_calendar(event_id)
        if not calendar:
            raise ValueError("Could not find calendar for event")

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
        if request.start_time is not None:
            vevent.dtstart.value = request.start_time
        if request.end_time is not None:
            vevent.dtend.value = request.end_time
        if request.location is not None:
            vevent.location.value = request.location
        if request.notes is not None:
            vevent.description.value = request.notes
        if request.url is not None:
            vevent.url.value = request.url
        if request.all_day is not None:
            if request.all_day:
                vevent.dtstart.value_param = 'DATE'
                vevent.dtend.value_param = 'DATE'
            else:
                vevent.dtstart.value_param = 'DATETIME'
                vevent.dtend.value_param = 'DATETIME'

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

        # Handle calendar change
        if request.calendar_name and request.calendar_name != calendar.name:
            new_calendar = self._find_calendar_by_name(request.calendar_name)
            if not new_calendar:
                raise ValueError(f"Calendar '{request.calendar_name}' not found")

            # Delete from old calendar and save to new one
            existing_event.delete()
            calendar = new_calendar

        try:
            # Save updated event
            event = calendar.save_event(vcal)
            logger.info(f"Successfully updated event: {request.title or vevent.summary.value}")
            return Event.from_caldav_event(event)

        except Exception as e:
            logger.error(f"Failed to update event: {e}")
            raise

    def delete_event(self, event_id: str) -> bool:
        """Delete an event by its identifier."""
        existing_event = self.find_event_by_id(event_id)
        if not existing_event:
            raise ValueError(f"Event with ID '{event_id}' not found")

        try:
            existing_event.delete()
            logger.info(f"Successfully deleted event with ID: {event_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to delete event: {e}")
            raise

    def find_event_by_id(self, event_id: str) -> Optional[CalendarObjectResource]:
        """Find an event by its identifier."""
        logger.debug(f"Searching for event with ID: {event_id}")

        # URL-decode the event_id to handle cases where it contains encoded characters
        decoded_event_id = unquote(event_id)
        logger.debug(f"Decoded event ID: {decoded_event_id}")

        calendars = self._get_all_calendars()

        for calendar in calendars:
            try:
                logger.debug(f"Searching calendar '{calendar.name}' for event ID '{event_id}'")
                events = calendar.events()
                logger.debug(f"Calendar '{calendar.name}' has {len(events)} total events")

                for event in events:
                    # Try multiple approaches to match the event
                    # 1. Try event.id (if available and not None)
                    if event.id is not None:
                        if event.id == event_id or event.id == decoded_event_id:
                            logger.debug(f"Found event via event.id match")
                            return event
                        # Also try comparing unquote of stored event ID
                        try:
                            if unquote(event.id) == decoded_event_id:
                                logger.debug(f"Found event via unquote(event.id) match")
                                return event
                        except Exception:
                            # If unquote fails, skip this comparison
                            pass

                    # 2. Try event.url (CalDAV often uses URL as the identifier)
                    if hasattr(event, 'url') and event.url:
                        event_url = str(event.url)
                        # Compare both encoded and decoded versions
                        if event_url == event_id or event_url == decoded_event_id:
                            logger.debug(f"Found event via event.url match")
                            return event
                        # Try unquoting the URL
                        try:
                            if unquote(event_url) == decoded_event_id:
                                logger.debug(f"Found event via unquote(event.url) match")
                                return event
                        except Exception:
                            pass

            except Exception as e:
                logger.error(f"Failed to search calendar {calendar.name} for event {event_id}: {e}")
                logger.debug(f"Calendar details that failed: name='{calendar.name}', url='{calendar.url}'")
                continue

        logger.debug(f"Event with ID '{event_id}' not found in any calendar")
        return None

    def list_calendar_names(self) -> List[str]:
        """List all available calendar names."""
        calendars = self._get_all_calendars()
        return [cal.name for cal in calendars]

    def _get_all_calendars(self) -> List[Calendar]:
        """Get all calendars from the CalDAV server."""
        try:
            calendars = self.principal.calendars()
            logger.debug(f"Successfully retrieved {len(calendars)} calendars")
            for cal in calendars:
                logger.debug(f"Calendar: name='{cal.name}', url='{cal.url}'")
            return calendars
        except Exception as e:
            logger.error(f"Failed to get calendars: {e}")
            logger.debug(f"Principal object: {self.principal}")
            logger.debug(f"Client object: {self.client}")
            return []

    def _find_calendar_by_name(self, calendar_name: str) -> Optional[Calendar]:
        """Find a calendar by name."""
        calendars = self._get_all_calendars()
        for calendar in calendars:
            if calendar.name == calendar_name:
                return calendar
        return None

    def _find_event_calendar(self, event_id: str) -> Optional[Calendar]:
        """Find which calendar contains the specified event."""
        # URL-decode the event_id to handle cases where it contains encoded characters
        decoded_event_id = unquote(event_id)

        for calendar in self._get_all_calendars():
            try:
                events = calendar.events()
                for event in events:
                    # Try multiple approaches to match the event
                    # 1. Try event.id (if available and not None)
                    if event.id is not None:
                        if event.id == event_id or event.id == decoded_event_id:
                            return calendar
                        # Also try comparing unquote of stored event ID
                        try:
                            if unquote(event.id) == decoded_event_id:
                                return calendar
                        except Exception:
                            # If unquote fails, skip this comparison
                            pass

                    # 2. Try event.url (CalDAV often uses URL as the identifier)
                    if hasattr(event, 'url') and event.url:
                        event_url = str(event.url)
                        # Compare both encoded and decoded versions
                        if event_url == event_id or event_url == decoded_event_id:
                            return calendar
                        # Try unquoting the URL
                        try:
                            if unquote(event_url) == decoded_event_id:
                                return calendar
                        except Exception:
                            pass
            except Exception as e:
                logger.warning(f"Failed to search calendar {calendar.name}: {e}")
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
            parts.append(f'UNTIL={recurrence_rule.end_date.strftime("%Y%m%dT%H%M%SZ")}')
        elif recurrence_rule.occurrence_count:
            parts.append(f'COUNT={recurrence_rule.occurrence_count}')

        return ';'.join(parts)