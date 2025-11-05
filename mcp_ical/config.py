import json
import os
from pathlib import Path
from typing import Any, Dict, Optional
from dataclasses import dataclass
from loguru import logger


@dataclass
class CalDAVConfig:
    server_url: str
    username: str
    password: str


@dataclass
class AppConfig:
    caldav: CalDAVConfig


class ConfigManager:
    """Configuration manager with support for config_private.json and config.json."""

    def __init__(self):
        self.project_root = Path(__file__).parent.parent.parent
        self.private_config_path = self.project_root / "config_private.json"
        self.default_config_path = self.project_root / "config.json"

    def load_config(self) -> AppConfig:
        """Load configuration with priority: config_private.json > config.json."""
        config_data = self._load_config_file()

        # Validate required CalDAV configuration
        if "caldav" not in config_data:
            raise ValueError("CalDAV configuration is required")

        caldav_config = CalDAVConfig(
            server_url=config_data["caldav"]["server_url"],
            username=config_data["caldav"]["username"],
            password=config_data["caldav"]["password"]
        )

        return AppConfig(caldav=caldav_config)

    def _load_config_file(self) -> Dict[str, Any]:
        """Load configuration from file with priority handling."""
        # Try config_private.json first
        if self.private_config_path.exists():
            logger.debug(f"Loading configuration from {self.private_config_path}")
            with open(self.private_config_path, 'r', encoding='utf-8') as f:
                return json.load(f)

        # Fall back to config.json
        if self.default_config_path.exists():
            logger.debug(f"Loading configuration from {self.default_config_path}")
            with open(self.default_config_path, 'r', encoding='utf-8') as f:
                return json.load(f)

        # No configuration found
        raise FileNotFoundError(
            f"No configuration file found. Please create either {self.private_config_path} "
            f"or {self.default_config_path}"
        )


# Global config manager instance
_config_manager = None


def get_config() -> AppConfig:
    """Get the application configuration."""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager.load_config()