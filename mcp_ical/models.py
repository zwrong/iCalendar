import re
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from typing import Annotated, Self, Optional

from caldav import CalendarObjectResource
from loguru import logger
from pydantic import BaseModel, BeforeValidator, Field, model_validator
import vobject


class Frequency(IntEnum):
    DAILY = 0
    WEEKLY = 1
    MONTHLY = 2
    YEARLY = 3


class Weekday(IntEnum):
    SUNDAY = 1
    MONDAY = 2
    TUESDAY = 3
    WEDNESDAY = 4
    THURSDAY = 5
    FRIDAY = 6
    SATURDAY = 7


def convert_datetime(v):
    """Convert various datetime formats to Python datetime objects."""
    if isinstance(v, datetime):
        return v

    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v)
        except ValueError:
            try:
                # Try to parse other common datetime formats
                return datetime.strptime(v, "%Y%m%dT%H%M%SZ")
            except ValueError:
                logger.warning(f"Could not parse datetime string: {v}")
                return v  # Let Pydantic handle the error

    if hasattr(v, 'value'):  # vobject datetime objects
        return v.value

    # If we don't recognize the type, let Pydantic handle it
    logger.debug(f"Unrecognized datetime type: {type(v)}, value: {v}")
    return v


FlexibleDateTime = Annotated[datetime, BeforeValidator(convert_datetime)]


class RecurrenceRule(BaseModel):
    frequency: Frequency
    interval: int = Field(default=1, ge=1)
    end_date: FlexibleDateTime | None = None
    occurrence_count: int | None = None
    days_of_week: list[Weekday] | None = None

    @model_validator(mode="after")
    def validate_end_conditions(self) -> Self:
        if self.end_date is not None and self.occurrence_count is not None:
            raise ValueError("Only one of end_date or occurrence_count can be set")
        return self

    @classmethod
    def from_ical_string(cls, rrule_str: str) -> Self:
        """Parse iCalendar RRULE string into RecurrenceRule."""
        parts = {}
        for part in rrule_str.split(';'):
            if '=' in part:
                key, value = part.split('=', 1)
                parts[key] = value

        frequency_map = {
            'DAILY': 0,
            'WEEKLY': 1,
            'MONTHLY': 2,
            'YEARLY': 3
        }

        frequency = frequency_map.get(parts.get('FREQ', 'DAILY'), 0)
        interval = int(parts.get('INTERVAL', '1'))

        end_date = None
        occurrence_count = None

        if 'UNTIL' in parts:
            # Parse UNTIL date (format: YYYYMMDDTHHMMSSZ)
            until_str = parts['UNTIL']
            try:
                if 'T' in until_str:
                    end_date = datetime.strptime(until_str, "%Y%m%dT%H%M%SZ")
                else:
                    end_date = datetime.strptime(until_str, "%Y%m%d")
            except ValueError:
                pass
        elif 'COUNT' in parts:
            occurrence_count = int(parts['COUNT'])

        days_of_week = None
        if 'BYDAY' in parts:
            weekday_map = {
                'SU': 1, 'MO': 2, 'TU': 3, 'WE': 4, 'TH': 5, 'FR': 6, 'SA': 7
            }
            days = []
            for day in parts['BYDAY'].split(','):
                if day in weekday_map:
                    days.append(weekday_map[day])
            if days:
                days_of_week = days

        return cls(
            frequency=frequency,
            interval=interval,
            end_date=end_date,
            occurrence_count=occurrence_count,
            days_of_week=days_of_week
        )


@dataclass
class Event:
    title: str
    start_time: FlexibleDateTime
    end_time: FlexibleDateTime
    identifier: str
    calendar_name: str | None = None
    location: str | None = None
    notes: str | None = None
    alarms_minutes_offsets: list[int] | None = None
    url: str | None = None
    all_day: bool = False
    has_alarms: bool = False
    organizer: str | None = None
    attendees: list[str] | None = None
    last_modified: FlexibleDateTime | None = None
    recurrence_rule: RecurrenceRule | None = None
    _raw_event: Optional[CalendarObjectResource] = None  # Store the original CalDAV event

    @classmethod
    def from_caldav_event(cls, caldav_event: CalendarObjectResource) -> "Event":
        """Create an Event instance from a CalDAV event."""
        event_id = getattr(caldav_event, 'id', None) or getattr(caldav_event, 'url', None) or 'unknown'
        logger.debug(f"Parsing event data for event {event_id}")

        # Handle both string and vobject data
        event_data = caldav_event.data
        if isinstance(event_data, str):
            # Parse string data into vobject
            vcal = vobject.readOne(event_data)
            logger.debug("Parsed string data into vobject")
        else:
            vcal = event_data
            logger.debug("Using existing vobject data")

        if not hasattr(vcal, 'vevent'):
            logger.error(f"Event data has no vevent component. Data type: {type(event_data)}")
            logger.debug(f"Event data content: {str(event_data)[:500]}...")
            raise ValueError(f"Event data has no vevent component")

        vevent = vcal.vevent
        logger.debug(f"Successfully extracted vevent for event {event_id}")

        # Basic properties
        title = getattr(vevent, 'summary', '').value if hasattr(vevent, 'summary') and vevent.summary else 'No Title'
        start_time = getattr(vevent, 'dtstart', '').value if hasattr(vevent, 'dtstart') and vevent.dtstart else None
        end_time = getattr(vevent, 'dtend', '').value if hasattr(vevent, 'dtend') and vevent.dtend else None

        location = None
        if hasattr(vevent, 'location') and vevent.location:
            location = vevent.location.value

        notes = None
        if hasattr(vevent, 'description') and vevent.description:
            notes = vevent.description.value

        url = None
        if hasattr(vevent, 'url') and vevent.url:
            url = vevent.url.value

        # Check if all-day event
        all_day = False
        if hasattr(vevent, 'dtstart') and vevent.dtstart:
            all_day = hasattr(vevent.dtstart, 'value_param') and vevent.dtstart.value_param == 'DATE'

        # Process alarms
        alarms = []
        if hasattr(vevent, 'valarm'):
            for alarm in vevent.getChildren():
                if alarm.name == 'VALARM' and hasattr(alarm, 'trigger') and alarm.trigger:
                    trigger = str(alarm.trigger.value)
                    # Parse trigger like "-PT15M" (15 minutes before)
                    match = re.match(r'-PT(\d+)([HM])', trigger)
                    if match:
                        amount = int(match.group(1))
                        unit = match.group(2)
                        if unit == 'H':
                            alarms.append(amount * 60)
                        else:  # M for minutes
                            alarms.append(amount)

        # Process recurrence rule
        recurrence = None
        if hasattr(vevent, 'rrule') and vevent.rrule:
            rrule_str = str(vevent.rrule.value)
            recurrence = RecurrenceRule.from_ical_string(rrule_str)

        # Get organizer
        organizer = None
        if hasattr(vevent, 'organizer') and vevent.organizer:
            organizer = vevent.organizer.value

        # Process attendees
        attendees = []
        if hasattr(vevent, 'attendee'):
            for attendee in vevent.getChildren():
                if attendee.name == 'ATTENDEE' and hasattr(attendee, 'cn') and attendee.cn:
                    attendees.append(attendee.cn.value)

        try:
            # Get last modified
            last_modified = None
            if hasattr(vevent, 'last_modified') and vevent.last_modified:
                last_modified = vevent.last_modified.value

            return cls(
                title=title,
                start_time=start_time,
                end_time=end_time,
                calendar_name=caldav_event.parent.name if hasattr(caldav_event, 'parent') and caldav_event.parent else None,
                location=location,
                notes=notes,
                url=url,
                all_day=all_day,
                alarms_minutes_offsets=alarms if alarms else None,
                recurrence_rule=recurrence,
                organizer=organizer,
                attendees=attendees if attendees else None,
                last_modified=last_modified,
                identifier=getattr(caldav_event, 'id', None) or getattr(caldav_event, 'url', None) or str(hash(str(caldav_event.data))),
                _raw_event=caldav_event,
            )

        except Exception as e:
            logger.error(f"Failed to parse CalDAV event {event_id}: {e}")
            logger.debug(f"CalDAV event data: {caldav_event.data}")
            raise ValueError(f"Failed to parse event {event_id}: {e}") from e

    def __str__(self) -> str:
        """Return a human-readable string representation of the Event."""
        attendees_list = ", ".join(self.attendees) if self.attendees else "None"
        alarms_list = ", ".join(map(str, self.alarms_minutes_offsets)) if self.alarms_minutes_offsets else "None"

        recurrence_info = "No recurrence"
        if self.recurrence_rule:
            frequency_names = {0: "DAILY", 1: "WEEKLY", 2: "MONTHLY", 3: "YEARLY"}
            recurrence_info = (
                f"Recurrence: {frequency_names.get(self.recurrence_rule.frequency, 'UNKNOWN')}, "
                f"Interval: {self.recurrence_rule.interval}, "
                f"End Date: {self.recurrence_rule.end_date or 'N/A'}, "
                f"Occurrences: {self.recurrence_rule.occurrence_count or 'N/A'}"
            )

        return (
            f"Event: {self.title},\n"
            f" - Identifier: {self.identifier},\n"
            f" - Start Time: {self.start_time},\n"
            f" - End Time: {self.end_time},\n"
            f" - Calendar: {self.calendar_name or 'N/A'},\n"
            f" - Location: {self.location or 'N/A'},\n"
            f" - Notes: {self.notes or 'N/A'},\n"
            f" - Alarms (minutes before): {alarms_list},\n"
            f" - URL: {self.url or 'N/A'},\n"
            f" - All Day Event?: {self.all_day},\n"
            f" - Organizer: {self.organizer or 'N/A'},\n"
            f" - Attendees: {attendees_list},\n"
            f" - {recurrence_info}\n"
        )


class CreateEventRequest(BaseModel):
    title: str
    start_time: datetime
    end_time: datetime
    calendar_name: str | None = None
    location: str | None = None
    notes: str | None = None
    alarms_minutes_offsets: list[int] | None = None
    url: str | None = None
    all_day: bool = False
    recurrence_rule: RecurrenceRule | None = None


class UpdateEventRequest(BaseModel):
    title: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    calendar_name: str | None = None
    location: str | None = None
    notes: str | None = None
    alarms_minutes_offsets: list[int] | None = None
    url: str | None = None
    all_day: bool | None = None
    recurrence_rule: RecurrenceRule | None = None
