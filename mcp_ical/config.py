import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

DEFAULT_SERVER_URL = "https://caldav.icloud.com/"

ENV_USERNAME = "ICLOUD_USERNAME"
ENV_PASSWORD = "ICLOUD_PASSWORD"
ENV_SERVER_URL = "CALDAV_SERVER_URL"
ENV_CONFIG_FILE = "MCP_ICAL_CONFIG"


@dataclass
class CalDAVConfig:
    server_url: str
    username: str
    # Never shown in repr(), so the password can't leak through a log of the config object
    password: str = field(repr=False)


@dataclass
class AppConfig:
    caldav: CalDAVConfig


class ConfigManager:
    """Configuration manager.

    Priority:
    1. Environment variables ICLOUD_USERNAME / ICLOUD_PASSWORD (+ optional CALDAV_SERVER_URL)
    2. JSON file pointed to by MCP_ICAL_CONFIG
    3. config_private.json, then config.json at the project root (legacy)
    """

    def __init__(self, environ: dict[str, str] | None = None):
        self.environ = os.environ if environ is None else environ
        self.project_root = Path(__file__).parent.parent
        self.private_config_path = self.project_root / "config_private.json"
        self.default_config_path = self.project_root / "config.json"

    def load_config(self) -> AppConfig:
        env_config = self._load_from_env()
        if env_config is not None:
            return AppConfig(caldav=env_config)

        config_data = self._load_config_file()
        caldav = config_data.get("caldav")
        if not isinstance(caldav, dict):
            raise ValueError("CalDAV configuration is required (missing 'caldav' section)")

        missing = [k for k in ("username", "password") if not caldav.get(k)]
        if missing:
            raise ValueError(f"CalDAV configuration is missing: {', '.join(missing)}")

        return AppConfig(
            caldav=CalDAVConfig(
                server_url=caldav.get("server_url") or DEFAULT_SERVER_URL,
                username=caldav["username"],
                password=caldav["password"],
            )
        )

    def _load_from_env(self) -> CalDAVConfig | None:
        username = self.environ.get(ENV_USERNAME)
        password = self.environ.get(ENV_PASSWORD)

        if not username and not password:
            return None
        if not username or not password:
            missing = ENV_USERNAME if not username else ENV_PASSWORD
            raise ValueError(
                f"Incomplete environment configuration: {missing} is not set. "
                f"Both {ENV_USERNAME} and {ENV_PASSWORD} are required "
                f"({ENV_SERVER_URL} is optional, default {DEFAULT_SERVER_URL})."
            )

        logger.debug(f"Loading configuration from environment ({ENV_USERNAME}={username})")
        return CalDAVConfig(
            server_url=self.environ.get(ENV_SERVER_URL) or DEFAULT_SERVER_URL,
            username=username,
            password=password,
        )

    def _load_config_file(self) -> dict[str, Any]:
        explicit = self.environ.get(ENV_CONFIG_FILE)
        if explicit:
            path = Path(explicit).expanduser()
            if not path.is_file():
                raise FileNotFoundError(f"{ENV_CONFIG_FILE} points to {path}, which does not exist")
            candidates = [path]
        else:
            candidates = [self.private_config_path, self.default_config_path]

        for path in candidates:
            if path.is_file():
                logger.debug(f"Loading configuration from {path}")
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)

        raise FileNotFoundError(
            f"No configuration found. Set {ENV_USERNAME} and {ENV_PASSWORD} "
            f"(optionally {ENV_SERVER_URL}), or point {ENV_CONFIG_FILE} to a JSON file, "
            f"or create {self.private_config_path}."
        )


def get_config() -> AppConfig:
    """Get the application configuration."""
    return ConfigManager().load_config()
