import sys
from datetime import datetime
from typing import List

from loguru import logger

from .caldav_client import CalDAVManager
from .models import (
    CreateEventRequest,
    Event,
    UpdateEventRequest,
)

logger.remove()
logger.add(
    sys.stderr,
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
    level="DEBUG",
)


class CalendarManager:
    """Calendar manager using CalDAV protocol for iCloud calendar access."""

    def __init__(self):
        """Initialize CalDAV manager."""
        try:
            self.caldav_manager = CalDAVManager()
            logger.info("Successfully initialized CalDAV calendar manager")
        except Exception as e:
            logger.error(f"Failed to initialize CalDAV manager: {e}")
            raise ValueError(f"Failed to connect to CalDAV server: {e}")

    def list_events(
        self,
        start_time: datetime,
        end_time: datetime,
        calendar_name: str | None = None,
    ) -> List[Event]:
        """List all events within a given date range

        Args:
            start_time: The start time of the date range
            end_time: The end time of the date range
            calendar_name: The name of the calendar to filter by

        Returns:
            List[Event]: A list of events within the date range
        """
        logger.info(
            f"Listing events between {start_time} - {end_time}, searching in: {calendar_name if calendar_name else 'all calendars'}"
        )

        return self.caldav_manager.list_events(start_time, end_time, calendar_name)

    def create_event(self, new_event: CreateEventRequest) -> Event:
        """Create a new calendar event

        Args:
            new_event: The event to create

        Returns:
            Event: The created event with identifier
        """
        logger.info(f"Creating event: {new_event.title}")
        return self.caldav_manager.create_event(new_event)

    def update_event(self, event_id: str, request: UpdateEventRequest) -> Event:
        """Update an existing event by its identifier

        Args:
            event_id: The unique identifier of the event to update
            request: The update request containing the fields to modify

        Returns:
            Event: The updated event if successful
        """
        logger.info(f"Updating event: {event_id}")
        return self.caldav_manager.update_event(event_id, request)

    def delete_event(self, event_id: str) -> bool:
        """Delete an event by its identifier

        Args:
            event_id: The unique identifier of the event to delete

        Returns:
            bool: True if deletion was successful

        Raises:
            ValueError: If the event with the given ID doesn't exist
            Exception: If there was an error deleting the event
        """
        logger.info(f"Deleting event: {event_id}")
        return self.caldav_manager.delete_event(event_id)

    def find_event_by_id(self, identifier: str) -> Event | None:
        """Find an event by its identifier

        Args:
            identifier: The unique identifier of the event

        Returns:
            Event | None: The event if found, None otherwise
        """
        logger.info(f"Finding event by ID: {identifier}")
        caldav_event = self.caldav_manager.find_event_by_id(identifier)
        if not caldav_event:
            logger.info(f"No event found with ID: {identifier}")
            return None

        return Event.from_caldav_event(caldav_event)

    def list_calendar_names(self) -> list[str]:
        """List all available calendar names

        Returns:
            list[str]: A list of calendar names
        """
        logger.info("Listing all calendar names")
        return self.caldav_manager.list_calendar_names()

    def list_calendars(self) -> list[str]:
        """List all available calendars

        Returns:
            list[str]: A list of calendar names
        """
        return self.list_calendar_names()

  