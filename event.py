#!/usr/bin/env python3
"""Create Google Calendar events from compact terminal-friendly time ranges."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - prerequisite is Python 3.11+
    tomllib = None  # type: ignore[assignment]


DEFAULTS: dict[str, Any] = {
    "defaults": {"event_name": "Event", "duration_mins": 60,
                 "open_browser": True, "calendar_id": "primary"},
    "flags": {
        "name": ["-n", "--name"], "day": ["-day", "--day"],
        "location": ["-l", "--location"], "description": ["-des", "--description"],
        "timezone": ["-tz", "--timezone"], "notification": ["-notif", "--notification"],
        "dry_run": ["-dr", "--dry-run"],
    },
    "timezones": {"ET": "America/New_York", "CT": "America/Chicago",
                  "MT": "America/Denver", "PT": "America/Los_Angeles",
                  "UTC": "UTC", "GMT": "UTC"},
}
DATE_LENGTHS = {0, 2, 4, 6, 8}
WINDOWS_ZONE_TO_IANA = {
    "Eastern Standard Time": "America/New_York",
    "Eastern Daylight Time": "America/New_York",
    "Central Standard Time": "America/Chicago",
    "Central Daylight Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver",
    "Mountain Daylight Time": "America/Denver",
    "Pacific Standard Time": "America/Los_Angeles",
    "Pacific Daylight Time": "America/Los_Angeles",
    "UTC": "UTC",
    "GMT Standard Time": "UTC",
}


class EventError(ValueError):
    """A friendly input or configuration error."""


@dataclass(frozen=True)
class TimePart:
    hour: int
    minute: int
    date_prefix: str
    timezone_abbr: str | None


@dataclass(frozen=True)
class ResolvedTime:
    value: datetime
    zone_name: str


def config_path() -> Path:
    return Path(os.environ.get("EVENT_CONFIG", Path.home() / ".config" / "event" / "event.toml"))


def load_config() -> dict[str, Any]:
    """Load the optional user config, merging its sections with documented defaults."""
    config = {key: value.copy() for key, value in DEFAULTS.items()}
    path = config_path()
    if not path.exists():
        return config
    if tomllib is None:
        raise EventError("Python 3.11+ is required to read TOML configuration.")
    try:
        with path.open("rb") as handle:
            supplied = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise EventError(f"Could not read config {path}: {exc}") from exc
    for section in ("defaults", "flags", "timezones"):
        value = supplied.get(section, {})
        if not isinstance(value, dict):
            raise EventError(f"[{section}] in {path} must be a TOML table.")
        config[section].update(value)
    return config


def build_parser(config: dict[str, Any]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("time_range", help="START..END, with END optional")
    flags = config["flags"]
    required = {"name", "day", "location", "description", "timezone", "notification", "dry_run"}
    if set(flags) != required:
        missing = ", ".join(sorted(required - set(flags)))
        extra = ", ".join(sorted(set(flags) - required))
        raise EventError(f"[flags] must define exactly the documented flags (missing: {missing or 'none'}; extra: {extra or 'none'}).")
    for name, options in flags.items():
        if not isinstance(options, list) or not options or not all(isinstance(x, str) and x.startswith("-") for x in options):
            raise EventError(f"[flags].{name} must be a non-empty list of flag names.")
        kwargs: dict[str, Any] = {"dest": name}
        if name == "dry_run":
            kwargs["action"] = "store_true"
        elif name == "notification":
            kwargs["type"] = int
        else:
            kwargs["metavar"] = name.upper()
        parser.add_argument(*options, **kwargs)
    return parser


def terminal_docs(config: dict[str, Any]) -> str:
    """Return the compact reference shown by the help flags."""
    flags = config["flags"]
    name = flags["name"][0]
    day = flags["day"][0]
    location = flags["location"][0]
    description = flags["description"][0]
    timezone = flags["timezone"][0]
    notification = flags["notification"][0]
    dry_run = flags["dry_run"][0]
    zones = ", ".join(sorted(str(zone).upper() for zone in config["timezones"]))
    return f"""event - create a Google Calendar event

Grammar:
  event START[..END] [{name} NAME] [{day} DATE] [{location} LOCATION] [{description} DESCRIPTION]
        [{timezone} ZONE] [{notification} MINUTES] [{dry_run}]
  TIME = [DD|MMDD|YYMMDD|YYYYMMDD]HHMM[ZONE]

Rules: HHMM uses 24-hour time; omit END for the configured default duration.
       Use either inline dates/zones or {day}/{timezone}, not both. Zones: {zones}.

Examples:
  event 1900.. -n "Standup"
  event 2100..2300 -n "Movie Night"
  event 09231900..09232100PT -n "Call" -dr
"""


def parse_time_part(token: str, zones: dict[str, str]) -> TimePart:
    match = re.fullmatch(r"(\d+)([A-Za-z]+)?", token)
    if not match:
        raise EventError(f"Invalid time token {token!r}; use digits followed by an optional timezone suffix.")
    digits, suffix = match.groups()
    abbr = suffix.upper() if suffix else None
    if abbr and abbr not in {str(key).upper() for key in zones}:
        supported = ", ".join(sorted(str(key).upper() for key in zones))
        raise EventError(f"Unrecognised timezone {suffix!r}. Supported values: {supported}.")
    date_digits, clock = digits[:-4], digits[-4:]
    if len(digits) < 4 or len(date_digits) not in DATE_LENGTHS:
        raise EventError(f"Unrecognised date format in {token!r}; date portion must have 0, 2, 4, 6, or 8 digits.")
    hour, minute = int(clock[:2]), int(clock[2:])
    if hour > 23 or minute > 59:
        raise EventError(f"Invalid time {clock!r}; expected a 24-hour HHMM value.")
    return TimePart(hour, minute, date_digits, abbr)


def upcoming_date(prefix: str, today: date) -> date:
    """Resolve DD/MMDD/YYMMDD/YYYYMMDD, choosing the earliest non-past date."""
    if not prefix:
        return today
    try:
        if len(prefix) == 2:
            candidate = date(today.year, today.month, int(prefix))
            if candidate < today:
                candidate = date(today.year + 1, today.month, int(prefix))
        elif len(prefix) == 4:
            candidate = date(today.year, int(prefix[:2]), int(prefix[2:]))
            if candidate < today:
                candidate = date(today.year + 1, candidate.month, candidate.day)
        elif len(prefix) == 6:
            candidate = date(2000 + int(prefix[:2]), int(prefix[2:4]), int(prefix[4:]))
        elif len(prefix) == 8:
            candidate = date(int(prefix[:4]), int(prefix[4:6]), int(prefix[6:]))
        else:  # protected by parse_time_part, also used for --day
            raise EventError("Date must use DD, MMDD, YYMMDD, or YYYYMMDD.")
    except ValueError as exc:
        raise EventError(f"Invalid calendar date {prefix!r}: {exc}") from exc
    return candidate


def system_timezone(zones: dict[str, str]) -> tuple[tzinfo, str]:
    # ``astimezone().tzinfo`` is commonly a fixed-offset object (especially on
    # Windows), whose label such as "MDT" is not a Calendar API timezone ID.
    # Prefer a configured IANA zone whose current abbreviation matches local.
    local = datetime.now().astimezone().tzinfo
    if local is None:  # defensive; astimezone normally always supplies one
        return timezone.utc, "UTC"
    local_label = time.tzname[1 if time.daylight and time.localtime().tm_isdst else 0]
    windows_iana = WINDOWS_ZONE_TO_IANA.get(local_label)
    if windows_iana and windows_iana in {str(value) for value in zones.values()}:
        return ZoneInfo(windows_iana), windows_iana
    for configured in zones.values():
        try:
            candidate = ZoneInfo(str(configured))
        except ZoneInfoNotFoundError:
            continue
        if datetime.now(candidate).tzname() == local_label:
            return candidate, str(configured)
    return local, getattr(local, "key", None) or str(local)


def get_zone(abbr: str | None, global_zone: str | None, zones: dict[str, str]) -> tuple[tzinfo, str]:
    if abbr is None and global_zone is None:
        return system_timezone(zones)
    key = (abbr or global_zone or "").upper()
    mapping = {str(k).upper(): str(v) for k, v in zones.items()}
    if key not in mapping:
        supported = ", ".join(sorted(mapping))
        raise EventError(f"Unrecognised timezone {key!r}. Supported values: {supported}.")
    try:
        return ZoneInfo(mapping[key]), mapping[key]
    except ZoneInfoNotFoundError as exc:
        raise EventError(f"Timezone {mapping[key]!r} is not available on this system.") from exc


def resolve(part: TimePart, shared_date: date | None, global_zone: str | None,
            zones: dict[str, str], today: date) -> ResolvedTime:
    day = upcoming_date(part.date_prefix, today) if part.date_prefix else (shared_date or today)
    zone, name = get_zone(part.timezone_abbr, global_zone, zones)
    return ResolvedTime(datetime(day.year, day.month, day.day, part.hour, part.minute, tzinfo=zone), name)


def make_payload(args: argparse.Namespace, config: dict[str, Any]) -> tuple[dict[str, Any], ResolvedTime, ResolvedTime]:
    if args.time_range.count("..") != 1:
        raise EventError("Time range must contain exactly one '..' separator.")
    start_text, end_text = args.time_range.split("..")
    if not start_text:
        raise EventError("A start time is required.")
    zones = config["timezones"]
    if not isinstance(zones, dict):
        raise EventError("[timezones] must be a TOML table.")
    start_part = parse_time_part(start_text, zones)
    end_part = parse_time_part(end_text, zones) if end_text else None
    parts = [part for part in (start_part, end_part) if part]
    timezone_flags = "/".join(config["flags"]["timezone"])
    day_flags = "/".join(config["flags"]["day"])
    if args.timezone and any(part.timezone_abbr for part in parts):
        raise EventError(f"Ambiguous timezone: use either {timezone_flags} or inline timezone suffixes, not both.")
    if args.day and any(part.date_prefix for part in parts):
        raise EventError(f"Ambiguous date: use either {day_flags} or inline dates, not both.")
    shared = upcoming_date(str(args.day), date.today()) if args.day else None
    today = date.today()
    start = resolve(start_part, shared, args.timezone, zones, today)
    if end_part:
        end = resolve(end_part, shared, args.timezone, zones, today)
    else:
        duration = config["defaults"].get("duration_mins", 60)
        if not isinstance(duration, int) or duration <= 0:
            raise EventError("defaults.duration_mins must be a positive integer.")
        end = ResolvedTime(start.value + timedelta(minutes=duration), start.zone_name)
    if start.value.astimezone(timezone.utc) >= end.value.astimezone(timezone.utc):
        raise EventError("Invalid range: start must be earlier than end.")
    defaults = config["defaults"]
    payload: dict[str, Any] = {
        "summary": args.name if args.name is not None else defaults.get("event_name", "Event"),
        "start": {"dateTime": start.value.isoformat(timespec="seconds"), "timeZone": start.zone_name},
        "end": {"dateTime": end.value.isoformat(timespec="seconds"), "timeZone": end.zone_name},
    }
    if args.location:
        payload["location"] = args.location
    if args.description:
        payload["description"] = args.description
    if args.notification is not None:
        if args.notification < 0:
            raise EventError("Notification minutes must be zero or greater.")
        payload["reminders"] = {"useDefault": False, "overrides": [{"method": "popup", "minutes": args.notification}]}
    return payload, start, end


def gws_command(payload: dict[str, Any], calendar_id: str) -> list[str]:
    return ["gws", "calendar", "events", "insert", "--params", json.dumps({"calendarId": calendar_id}), "--json", json.dumps(payload)]


def open_browser(link: str) -> None:
    if sys.platform == "win32":
        subprocess.run(["powershell", "-NoProfile", "-Command", "Start-Process", link], check=False)
    elif sys.platform == "darwin":
        subprocess.run(["open", link], check=False)
    else:
        subprocess.run(["xdg-open", link], check=False)


def display_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M %Z")


def main(argv: list[str] | None = None) -> int:
    try:
        config = load_config()
        command_args = sys.argv[1:] if argv is None else argv
        if command_args in (["-h"], ["--help"]):
            print(terminal_docs(config), end="")
            return 0
        args = build_parser(config).parse_args(command_args)
        payload, start, end = make_payload(args, config)
        calendar_id = str(config["defaults"].get("calendar_id", "primary"))
        command = gws_command(payload, calendar_id)
        if args.dry_run:
            print("[dry-run] " + subprocess.list2cmdline(command))
            return 0
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
            raise EventError(f"gws failed: {detail}")
        try:
            response = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise EventError(f"gws returned invalid JSON: {completed.stdout.strip()}") from exc
        name = payload["summary"]
        print(f'✓ Event created: "{name}"')
        print(f"  Start:    {display_time(start.value)}")
        print(f"  End:      {display_time(end.value)}")
        if response.get("htmlLink"):
            print(f"  Link:     {response['htmlLink']}")
        if response.get("id"):
            print(f"  Event ID: {response['id']}")
        if config["defaults"].get("open_browser", True) and response.get("htmlLink"):
            open_browser(response["htmlLink"])
        return 0
    except EventError as exc:
        print(f"event: error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print("event: error: gws was not found on PATH. Install and authenticate gws first.", file=sys.stderr)
        return 127


if __name__ == "__main__":
    raise SystemExit(main())
