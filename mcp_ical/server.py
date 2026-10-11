import logging
import os
import sys
from datetime import datetime
from functools import cache
from textwrap import dedent

from loguru import logger
from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

from .ical import CalendarManager
from .models import CreateEventRequest, UpdateEventRequest

ENV_LOG_LEVEL = "MCP_ICAL_LOG_LEVEL"
ENV_READ_ONLY = "MCP_ICAL_READ_ONLY"

LOG_FORMAT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}"


def configure_logging(level: str | None = None) -> None:
    """Single place where loguru is configured. Logs go to stderr: stdout carries the MCP stdio protocol."""
    level = (level or os.environ.get(ENV_LOG_LEVEL) or "INFO").upper()
    logger.remove()
    try:
        logger.add(sys.stderr, format=LOG_FORMAT, level=level)
    except ValueError:
        logger.add(sys.stderr, format=LOG_FORMAT, level="INFO")
        logger.warning(f"Unknown {ENV_LOG_LEVEL}={level!r}, falling back to INFO")
        level = "INFO"

    # caldav logs its iCalendar fix-ups as warnings that include a diff of the event (title, notes...):
    # only let them through when debugging.
    debugging = level in ("TRACE", "DEBUG")
    logging.getLogger("caldav").setLevel(logging.DEBUG if debugging else logging.ERROR)


def is_read_only(environ=None) -> bool:
    value = (os.environ if environ is None else environ).get(ENV_READ_ONLY, "")
    return value.strip().lower() in ("1", "true", "yes", "on")


# Initialize the CalendarManager on demand in order to only establish CalDAV connection
# when a calendar tool is invoked instead of when the MCP client starts.
@cache
def get_calendar_manager() -> CalendarManager:
    """Get or initialize the calendar manager with proper error handling."""
    try:
        return CalendarManager()
    except ValueError as e:
        error_msg = dedent("""\
        CalDAV connection failed. Please check your configuration:

        1. Set ICLOUD_USERNAME and ICLOUD_PASSWORD (or MCP_ICAL_CONFIG / config_private.json)
        2. Verify your Apple ID and app-specific password are correct
        3. Check your network connection
        4. Ensure iCloud Calendar is enabled for your account
        """)
        raise ValueError(error_msg) from e


def get_calendars() -> str:
    """List all available calendars that can be used with calendar operations."""
    try:
        manager = get_calendar_manager()
        calendars = manager.list_calendar_names()
        if not calendars:
            return "No calendars found"
        return "Available calendars:\n" + "\n".join(f"- {cal}" for cal in calendars)
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Error listing calendars: {e!s}"


async def list_calendars() -> str:
    """List all available calendars."""
    try:
        manager = get_calendar_manager()
        calendars = manager.list_calendar_names()
        if not calendars:
            return "No calendars found"

        return "Available calendars:\n" + "\n".join(f"- {calendar}" for calendar in calendars)

    except Exception as e:
        return f"Error listing calendars: {e!s}"


async def list_events(start_date: datetime, end_date: datetime, calendar_name: str | None = None) -> str:
    """List calendar events in a date range.

    The start_date should always use the time such that it represents the beginning of that day (00:00:00).
    The end_date should always use the time such that it represents the end of that day (23:59:59).
    This way, range based searches are always inclusive and can locate all events in that date range.

    Args:
        start_date: Start date in ISO8601 format (YYYY-MM-DDT00:00:00).
        end_date: Optional end date in ISO8601 format (YYYY-MM-DDT23:59:59).
        calendar_name: Optional calendar name to filter by
    """
    try:
        logger.info(f"List events request: start={start_date}, end={end_date}, calendar={calendar_name}")
        manager = get_calendar_manager()
        events = manager.list_events(start_date, end_date, calendar_name)

        if not events:
            # Provide more detailed information about why no events were found
            calendars = manager.list_calendar_names()
            calendar_info = f" searched in calendar: {calendar_name}" if calendar_name else f" searched in all {len(calendars)} calendars: {', '.join(calendars)}"

            return f"No events found in the specified date range.\n" \
                   f"Searched period: {start_date.date()} to {end_date.date()}\n" \
                   f"Available calendars: {', '.join(calendars)}\n" \
                   f"Search scope: {calendar_info}\n" \
                   f"Tip: Check if events exist in this date range and that you have permissions to access the calendar."

        logger.info(f"Successfully found {len(events)} events")
        return "\n".join(str(event) for event in events)

    except ValueError as e:
        # Handle known configuration issues
        error_msg = str(e)
        if "Failed to connect" in error_msg or "CalDAV connection failed" in error_msg:
            return error_msg
        return f"Configuration error: {error_msg}"
    except Exception as e:
        logger.error(f"Unexpected error in list_events: {e}")
        return f"Error listing events: {e!s}\n" \
               f"This could be due to:\n" \
               f"- Network connectivity issues\n" \
               f"- Calendar access permissions\n" \
               f"- Invalid date range\n" \
               f"Please check your configuration and try again."


async def create_event(create_event_request: CreateEventRequest) -> str:
    """Create a new calendar event.

    Before using this tool, make sure to:
    1. Ask the user which calendar they want to use if not specified (check calendars://list)
    2. Ask if they want to add a location if none provided
    3. Ask if they want to add any notes/description if none provided
    4. Confirm the date and time with the user
    5. Ask if they want to set reminders for the event

    Args:
        title: Event title
        start_time: Start time in ISO format (YYYY-MM-DDTHH:MM:SS)
        end_time: End time in ISO format (YYYY-MM-DDTHH:MM:SS)
        notes: Optional event notes/description. Ask user if they want to add notes.
        location: Optional event location. Ask user if they want to specify a location.
        calendar_name: Optional calendar name. Ask user which calendar to use, referencing calendars://list.
        all_day: Whether this is an all-day event
        reminder_offsets: List of minutes before the event to trigger reminders\
            e.g. [60, 1440] means two reminders, the first 24 hours before the event and the second one hour before.
        recurrence_rule: Optional recurrence rule for the event. This should be an instance of `RecurrenceRule` with the following fields:
            - frequency: Frequency of the recurrence (e.g., DAILY, WEEKLY, MONTHLY, YEARLY).
            - interval: Interval between recurrences (e.g., every 2 weeks).
            - end_date: Optional end date for the recurrence. If specified, the recurrence will stop on this date.
            - occurrence_count: Optional number of occurrences. If specified, the recurrence will stop after this many occurrences.
            - days_of_week: Optional list of weekdays for the event. Use integers to represent days:
                - Sunday: 1
                - Monday: 2
                - Tuesday: 3
                - Wednesday: 4
                - Thursday: 5
                - Friday: 6
                - Saturday: 7

    Note: Both `end_date` and `occurrence_count` should not be set simultaneously; choose one or the other, or leave both unset.
    """
    logger.debug(f"Incoming Create Event Request: {create_event_request}")
    try:
        manager = get_calendar_manager()

        event = manager.create_event(create_event_request)
        if not event:
            return "Failed to create event. Please check calendar permissions and try again."

        return f"Successfully created event: {event.title} (ID: {event.identifier})"

    except Exception as e:
        return f"Error creating event: {e!s}"


async def update_event(event_id: str, update_event_request: UpdateEventRequest) -> str:
    """Update an existing calendar event.

    Before using this tool, make sure to:
    1. Ask the user which fields they want to update
    2. If moving to a different calendar, verify the calendar exists using calendars://list
    3. If updating time, confirm the new time with the user
    4. Ask if they want to add/update location if not specified
    5. Ask if they want to add/update notes if not specified
    6. Ask if they want to set reminders for the event

    Args:
        event_id: Unique identifier of the event to update
        title: Optional new title
        start_time: Optional new start time in ISO format
        end_time: Optional new end time in ISO format
        notes: Optional new notes/description. Ask user if they want to update notes.
        location: Optional new location. Ask user if they want to specify/update location.
        calendar_name: Optional new calendar. Ask user which calendar to use, referencing calendars://list.
        all_day: Optional all-day flag
        reminder_offsets: List of minutes before the event to trigger reminders\
            e.g. [60, 1440] means two reminders, the first 24 hours before the event and the second one hour before.
        recurrence_rule: Optional recurrence rule for the event. This should be an instance of `RecurrenceRule` with the following fields:
            - frequency: Frequency of the recurrence (e.g., DAILY, WEEKLY, MONTHLY, YEARLY).
            - interval: Interval between recurrences (e.g., every 2 weeks).
            - end_date: Optional end date for the recurrence. If specified, the recurrence will stop on this date.
            - occurrence_count: Optional number of occurrences. If specified, the recurrence will stop after this many occurrences.
            - days_of_week: Optional list of weekdays for the event. Use integers to represent days:
                - Sunday: 1
                - Monday: 2
                - Tuesday: 3
                - Wednesday: 4
                - Thursday: 5
                - Friday: 6
                - Saturday: 7

    Note: Both `end_date` and `occurrence_count` should not be set simultaneously; choose one or the other, or leave both unset.
    """
    try:
        manager = get_calendar_manager()
        event = manager.update_event(event_id, update_event_request)
        if not event:
            return f"Failed to update event. Event with ID {event_id} not found or update failed."

        return f"Successfully updated event: {event.title}"

    except Exception as e:
        return f"Error updating event: {e!s}"


async def delete_event(event_id: str) -> str:
    """Delete a calendar event by its identifier.

    Before using this tool, make sure to:
    1. Confirm with the user that they want to delete the event
    2. Verify the event exists by listing events first
    3. Note that this action cannot be undone

    Args:
        event_id: Unique identifier of the event to delete
    """
    try:
        manager = get_calendar_manager()
        success = manager.delete_event(event_id)

        if success:
            return f"Successfully deleted event with ID: {event_id}"
        else:
            return f"Failed to delete event with ID: {event_id}"

    except Exception as e:
        return f"Error deleting event: {e!s}"


async def debug_calendar_connection() -> str:
    """Debug calendar connection and provide detailed diagnostic information.

    This tool helps diagnose why events might not be showing up.
    It will test:
    1. CalDAV connection status
    2. Calendar accessibility
    3. Event search capabilities
    4. Recent events count
    """
    try:
        logger.info("Starting calendar connection debug...")

        # Test connection
        manager = get_calendar_manager()
        calendars = manager.list_calendar_names()

        debug_info = [
            "=== Calendar Connection Debug ===",
            "✓ CalDAV connection successful",
            f"✓ Found {len(calendars)} calendars: {', '.join(calendars)}",
            ""
        ]

        # Test each calendar
        from datetime import datetime, timedelta
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)  # Last 7 days

        total_events = 0
        for calendar_name in calendars:
            try:
                events = manager.list_events(start_date, end_date, calendar_name)
                total_events += len(events)
                debug_info.append(f"Calendar '{calendar_name}': {len(events)} events in last 7 days")

                # Show sample events (up to 2 per calendar)
                for event in events[:2]:
                    debug_info.append(f"  - {event.title} ({event.start_time.date()})")

            except Exception as e:
                debug_info.append(f"Calendar '{calendar_name}': ERROR - {e!s}")

        debug_info.extend([
            "",
            f"Total events found in last 7 days: {total_events}",
            "",
            "If you see 0 events above, this could mean:",
            "- No events exist in the last 7 days",
            "- Calendar access permission issues",
            "- Events are in different calendars",
            "- Date/time format issues",
            "",
            "Try searching a wider date range or check specific calendar permissions."
        ])

        logger.info(f"Debug completed: {len(calendars)} calendars, {total_events} total events")
        return "\n".join(debug_info)

    except ValueError as e:
        if "Failed to connect" in str(e) or "CalDAV connection failed" in str(e):
            return f"✗ CalDAV Connection Failed: {e!s}\n\nPlease check your configuration (ICLOUD_USERNAME / ICLOUD_PASSWORD) and ensure:\n- Server URL is correct (https://caldav.icloud.com/)\n- Apple ID and app-specific password are correct\n- iCloud Calendar is enabled for your account"
        return f"Configuration error: {e!s}"
    except Exception as e:
        logger.error(f"Debug failed with error: {e}")
        return f"✗ Debug failed: {e!s}\n\nThis indicates a serious configuration or connectivity issue."


READ_TOOLS = (list_calendars, list_events, debug_calendar_connection)
WRITE_TOOLS = (create_event, update_event, delete_event)


def create_server(read_only: bool = False) -> MCPServer:
    """Build the MCP server. In read-only mode the write tools are not registered at all."""
    server = MCPServer("Calendar")
    server.resource("calendars://list")(get_calendars)

    for fn in READ_TOOLS:
        server.add_tool(fn, annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True))

    if read_only:
        logger.info(f"Read-only mode ({ENV_READ_ONLY}): write tools are not registered")
    else:
        server.add_tool(create_event, annotations=ToolAnnotations(destructive_hint=False, open_world_hint=True))
        server.add_tool(update_event, annotations=ToolAnnotations(destructive_hint=True, open_world_hint=True))
        server.add_tool(delete_event, annotations=ToolAnnotations(destructive_hint=True, open_world_hint=True))

    return server


def main():
    configure_logging()
    server = create_server(read_only=is_read_only())
    logger.info("Running mcp-ical server...")
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
