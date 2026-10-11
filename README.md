# iCloud Calendar MCP Server

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A [Model Context Protocol](https://modelcontextprotocol.io/) server that gives MCP clients access to iCloud Calendar
over CalDAV: list calendars, read, create, update, move and delete events. It works on Linux, macOS and Windows,
since it talks to iCloud directly instead of going through the macOS Calendar app.

It started as an iCloud port of the macOS `mcp-ical` server. See the [changelog](#changelog) for what changed in 0.2.0.

## Tools

| Tool | Description |
|---|---|
| `list_calendars` | Names of the available calendars |
| `list_events` | Events in a date range, optionally for one calendar, sorted by start time |
| `create_event` | New event: title, times, all-day, location, notes, URL, alarms, recurrence |
| `update_event` | Change any of those fields, or move the event to another calendar |
| `delete_event` | Delete an event by identifier |
| `debug_calendar_connection` | Connection check: calendars and event counts over the last 7 days |

The `calendars://list` resource also lists the calendars. With `MCP_ICAL_READ_ONLY=true`, the three write tools are
not registered at all.

## Requirements

- Python 3.12 or later and [uv](https://docs.astral.sh/uv/)
- An Apple ID with iCloud Calendar enabled
- An **app-specific password** for that Apple ID

### App-specific password

1. Sign in to [appleid.apple.com](https://appleid.apple.com/), open **Sign-In and Security**, then
   **App-Specific Passwords**.
2. Generate a password, for example named `mcp-ical`.

The username is your Apple ID address, which is not necessarily an `@icloud.com` address.
An app-specific password gives access to iCloud CalDAV, CardDAV and IMAP: keep it out of version control and revoke
it if it leaks.

## Installation

```bash
git clone https://github.com/zwrong/iCalendar.git mcp-ical
cd mcp-ical
uv sync --no-dev
```

## Configuration

Credentials are read in this order:

1. **Environment variables** (recommended)

   | Variable | Required | Default |
   |---|---|---|
   | `ICLOUD_USERNAME` | yes | |
   | `ICLOUD_PASSWORD` | yes | |
   | `CALDAV_SERVER_URL` | no | `https://caldav.icloud.com/` |

2. **`MCP_ICAL_CONFIG`**: path to a JSON file shaped like [`config.json.example`](config.json.example)
3. **`config_private.json`**, then **`config.json`**, at the repository root (kept for compatibility)

Other settings:

| Variable | Default | Effect |
|---|---|---|
| `MCP_ICAL_READ_ONLY` | `false` | `true` leaves out `create_event`, `update_event` and `delete_event` |
| `MCP_ICAL_LOG_LEVEL` | `INFO` | `TRACE`, `DEBUG`, `INFO`, `WARNING` or `ERROR`. Logs go to stderr |

The server speaks MCP over stdio and is started by the client. To check it by hand:

```bash
ICLOUD_USERNAME=you@example.com ICLOUD_PASSWORD=xxxx-xxxx-xxxx-xxxx uv run mcp-ical
```

It waits on stdin and prints nothing on stdout.

## Client setup

Use the absolute path of `uv` (`which uv`): clients often start servers without your shell `PATH`, which shows up
as `spawn uv ENOENT`.

### Keeping the password out of the client config

Most clients store `env` values in clear text in their config file. A small wrapper that reads an env file avoids
that:

```bash
mkdir -p ~/.config/mcp-ical && chmod 700 ~/.config/mcp-ical
cat > ~/.config/mcp-ical/env <<'EOF'
ICLOUD_USERNAME=you@example.com
ICLOUD_PASSWORD=xxxx-xxxx-xxxx-xxxx
MCP_ICAL_READ_ONLY=true
EOF
chmod 600 ~/.config/mcp-ical/env

cat > ~/.local/bin/mcp-ical-run <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
set -a; source "$HOME/.config/mcp-ical/env"; set +a
exec /absolute/path/to/uv --directory /absolute/path/to/mcp-ical run --frozen --no-dev mcp-ical
EOF
chmod +x ~/.local/bin/mcp-ical-run
```

Then point the client at `~/.local/bin/mcp-ical-run`. Starting read-only and switching to `false` once reading works
is a sensible first step.

### Claude Desktop

In `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "icloud-calendar": {
      "command": "/absolute/path/to/uv",
      "args": ["--directory", "/absolute/path/to/mcp-ical", "run", "--frozen", "--no-dev", "mcp-ical"],
      "env": {
        "ICLOUD_USERNAME": "you@example.com",
        "ICLOUD_PASSWORD": "xxxx-xxxx-xxxx-xxxx"
      }
    }
  }
}
```

or, with the wrapper, `"command": "/home/you/.local/bin/mcp-ical-run"` and no `env`.

### Claude Code

```bash
claude mcp add --scope user icloud-calendar -- ~/.local/bin/mcp-ical-run
```

`/mcp` in a session shows the server state and its tools.

### Other clients

Any MCP client that can start a stdio server works: run `uv --directory /path/to/mcp-ical run mcp-ical` with the
environment variables above.

## Behavior worth knowing

- **Times.** Event times are written in UTC. A time with an offset (`2030-01-07T09:30:00+01:00`) is converted, and a
  time without one is taken as the machine's local time. Times are shown back in the machine's timezone. All-day
  events are written as dates, with the end date exclusive as the iCalendar standard requires.
- **Moving between calendars.** iCloud refuses the same event UID in two calendars, so a move cannot be
  "copy then delete". `update_event` with `calendar_name` uses a WebDAV `MOVE`, which is atomic on the server: if it
  fails, the event stays where it was. A move without other changes keeps the original iCalendar data untouched.
- **Finding events.** Listings fill an in-memory index (UID to URL) that lives as long as the server process.
  Lookups then go straight to the event, falling back to the `<calendar>/<UID>.ics` URL iCloud uses for most
  events, and only then to a scan of all calendars. iCloud does not support CalDAV UID searches.
- **Volume.** iCloud returns any number of events per request. The practical limit is the client: many cap a tool
  result at around 25,000 tokens, roughly 300 events in this server's output, so split long ranges.
- **Scope.** Only calendars hosted on iCloud are visible. Exchange, Google or other accounts added to the Calendar
  app on a device are not reachable through iCloud CalDAV, and neither are read-only subscribed (ICS) calendars.

## Privacy and security

- The only network destination is the configured CalDAV server; there is no telemetry.
- The password never appears in logs, at any level, nor in the configuration object's `repr`.
- Logs go to stderr only, since stdout carries the MCP protocol. Event contents (titles, notes) are logged only at
  `DEBUG` or below; at `INFO`, only identifiers. Some clients save server stderr to disk, so keep `DEBUG` for
  troubleshooting.
- Dependencies are locked in `uv.lock` and resolved from the official PyPI index only.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `401 Unauthorized` | Regular Apple ID password instead of an app-specific one, or a revoked one |
| `spawn uv ENOENT` | `uv` is not on the client's `PATH`: use its absolute path |
| No events returned | Wrong date range, an event in a non-iCloud or subscribed calendar, or a timezone mix-up |
| Server does not respond | Something writes to stdout and breaks the stdio protocol |
| `Calendar 'X' not found` | Calendar names are case-sensitive: check `list_calendars` |

`debug_calendar_connection` and `MCP_ICAL_LOG_LEVEL=DEBUG` help narrow things down.

## Development

```bash
uv sync
uv run pytest -m "not integration"   # unit tests, CalDAV client mocked, no network
uv run ruff check .
```

Integration tests run against a real account, read-only, and are skipped unless credentials are set:

```bash
ICLOUD_USERNAME=... ICLOUD_PASSWORD=... uv run pytest -m integration
```

Project layout:

```
mcp_ical/
├── server.py          # MCP server, tool registration, logging setup
├── caldav_client.py   # CalDAV access: search, create, update, move, delete, event index
├── ical.py            # Thin calendar manager used by the server
├── models.py          # Event and request models, iCalendar parsing
└── config.py          # Configuration loading
tests/                 # Unit tests
tests/integration/     # Read-only tests against a real account
```

## Changelog

### 0.2.0

- **Dependencies.** Lockfile regenerated from the official PyPI index (it resolved packages through a third-party
  mirror over plain HTTP). Upgrade to `mcp` 2.x (`FastMCP` became `MCPServer`) and `caldav` 3.x; the original code no
  longer starts against current releases.
- **Configuration.** Environment variables, `MCP_ICAL_CONFIG`, read-only mode, configurable log level, no event
  contents in logs above `DEBUG`.
- **Fixes.**
  - Creating an event with a timezone offset failed (`Unable to guess TZID`); all-day events and recurrence end
    dates were written incorrectly.
  - Alarms were never read back.
  - Updating an event without a location, notes or URL failed; changing its time produced invalid iCalendar data.
  - Moving an event deleted it before saving the copy, so a failed save lost it.
  - New events got a UID containing spaces and the machine's hostname.
- **Behavior.** Events sorted by start time, compact output without empty fields, in-memory event index.
- **Tests.** Unit tests for configuration, read-only mode, logging, iCalendar output, moves and lookups.

## Credits and license

MIT, see [LICENSE](LICENSE).

- The original macOS `mcp-ical` server (Omar D.), whose copyright notice is kept in `LICENSE`
- [python-caldav](https://github.com/python-caldav/caldav) and the
  [Model Context Protocol](https://modelcontextprotocol.io/) Python SDK
