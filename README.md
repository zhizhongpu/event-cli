# `event` — Terminal Calendar CLI

A Python CLI for creating Google Calendar events via `gws`, with smart time/date parsing and optional AI integration.

Run `event --help` (or `event -h`) for a concise terminal grammar and example reference.

---

## Table of Contents

- [`event` — Terminal Calendar CLI](#event--terminal-calendar-cli)
  - [Table of Contents](#table-of-contents)
  - [Prerequisites](#prerequisites)
  - [Quick Examples](#quick-examples)
  - [Installation \& Config](#installation--config)
  - [Arguments \& Flags](#arguments--flags)
  - [Time \& Date Syntax](#time--date-syntax)
    - [Time Format](#time-format)
    - [Timezone Suffix](#timezone-suffix)
    - [Date Prefix](#date-prefix)
    - [Time Range (`..`)](#time-range-)
    - [Inline Date+Time Combined](#inline-datetime-combined)
  - [Defaults](#defaults)
  - [Conflict \& Error Rules](#conflict--error-rules)
  - [Dry Run](#dry-run)
  - [Notification](#notification)
  - [Browser Behaviour](#browser-behaviour)
  - [Output](#output)
  - [TOML Config](#toml-config)

---

## Prerequisites

| Prerequisite | What it is | Verify with |
|---|---|---|
| **Python 3.14.6+** | Runtime for the script. 3.11+ required for stdlib `tomllib`. | `python --version` |
| **gws 0.22.5+** | Google Workspace CLI that talks to Google Calendar. | `gws --version` |
| **uv 0.11.23+** | Installs the bundled `tzdata` dependency for IANA timezone support on every platform. | `uv --version` |

> If `python` resolves to Python 2 on your system, substitute `python3` and update your shell alias accordingly.

---

## Quick Examples

```bash
# Minimal: named event today 19:00–20:00 (default 1hr)
event Event 1900..

# Named event, explicit end time
event "Movie Night" 2100..2300

# With location and description
event Dinner 1900..2100 -l "Snooze Restaurant" -des "Birthday dinner for Alex"

# Specific day this month (soonest upcoming 25th)
event "Team Sync" 1900..2100 -day 25

# Specific month+day (soonest upcoming Sep 23)
event Flight 1400..1500 -day 0923

# Specific full date
event Conference 0900..1700 -day 20260923

# Cross-timezone range
event "Call with SF team" 1900MT..2100PT

# Global timezone override
event Call 1900..2100 -tz PT

# Inline date inside time string (no -day needed)
event "Multi-day boundary" 202609231900..202609232100

# With notification
event Standup 1900.. -notif 10

# Dry run — prints gws command, does not submit, does not open browser
event Test 1900..2100 -dr

# Full example
event Gaming 1745..2100 -day 21 -l "Home" -des "Play AoE2" -tz MT -notif 15

# Cross-date: today 21:00 to 2027-01-20 01:00
event "New Year's Eve" 192100..202701200100
```

---

## Installation & Config

1. Clone or download the script.
2. Ensure `gws` is installed and authenticated.
3. Copy `event.toml` to `~/.config/event/event.toml` (or set `EVENT_CONFIG` env var to a custom path).
4. Run it with uv (or add an alias): `alias event="uv run --project /path/to/event-cli /path/to/event-cli/event.py"` or the following powershell alias:
 
```powershell
function event {
    uv run --project "C:\path\to\event-cli" "C:\path\to\event-cli\event.py" @args
}
```

---

## Arguments & Flags

| Flag | Long form | Description |
|------|-----------|-------------|
| *(first positional)* | | Event name (required). Quote names containing spaces. |
| *(second positional)* | | Time range string (required). See [Time & Date Syntax](#time--date-syntax). |
| `-day` | `--day` | Date shared by both times. See [Date Prefix](#date-prefix). |
| `-l` | `--location` | Location string or URL. Free text. |
| `-des` | `--description` | Event description. Free text. |
| `-tz` | `--timezone` | Global timezone (applies to any time that has no inline tz). |
| `-notif` | `--notification` | Single popup reminder, in minutes before event start. |
| `-dr` | `--dry-run` | Print the `gws` command; do not submit or open browser. |

> Flag names are configurable in `event.toml` without editing source code.

---

## Time & Date Syntax

### Time Format

Times are always exactly **4 digits**, 24-hour `HHMM`. No colons, no separators.

| Input | Meaning |
|-------|---------|
| `1900` | 19:00 |
| `1945` | 19:45 |
| `0930` | 09:30 |
| `0000` | midnight |

> 2-digit and 3-digit times are **not accepted**. Always zero-pad: `0900` not `9` or `900`.

---

### Timezone Suffix

A timezone abbreviation is appended directly to a time token with no space or separator:

| Input | Meaning |
|-------|---------|
| `1900PT` | 19:00 Pacific Time |
| `1930MT` | 19:30 Mountain Time |
| `0900ET` | 09:00 Eastern Time |
| `1900` | system timezone (or `-tz` override if supplied) |

The parser strips the tz suffix first, then reads the remaining 4 digits as HHMM.

Supported abbreviations (case-insensitive):

| Abbreviation | Zone |
|---|---|
| `ET` | America/New_York |
| `CT` | America/Chicago |
| `MT` | America/Denver |
| `PT` | America/Los_Angeles |
| `UTC` | UTC |
| `GMT` | UTC |

> More abbreviations can be added in `event.toml` under `[timezones]`.

---

### Date Prefix

A date can be embedded directly before the 4-digit time with no separator. The parser peels off the last 4 digits as HHMM; everything before is the date.

| Total digits (after tz strip) | Date portion | Time portion |
|---|---|---|
| 4 | none — uses `-day` or today | HHMM |
| 6 | DD (day of month, soonest upcoming) | HHMM |
| 8 | MMDD (soonest upcoming) | HHMM |
| 10 | YYMMDD | HHMM |
| 12 | YYYYMMDD | HHMM |

**Date disambiguation rule:** build the date from the smallest unit first. The rightmost unit is always day; only when day is present is month considered; only when both are present is year considered.

| Input | Date portion | Interpretation |
|-------|-------------|----------------|
| `231900` | `23` | Soonest upcoming 23rd |
| `09231900` | `0923` | Soonest upcoming Sep 23 |
| `260923_1900` | `260923` | 2026-09-23 (YYMMDD) |
| `202609231900` | `20260923` | 2026-09-23 (YYYYMMDD) |

---

### Time Range (`..`)

`..` separates start and end. End may be omitted to use default duration.

| Input | Meaning |
|-------|---------|
| `1900..2100` | 19:00 → 21:00 (system tz) |
| `1900..` | 19:00 → 20:00 (default duration from TOML, default 60 min) |
| `1900PT..2100` | 19:00 PT → 21:00 system tz |
| `1900MT..2100PT` | 19:00 MT → 21:00 PT |
| `1945..2014` | 19:45 → 20:14 |

---

### Inline Date+Time Combined

Date and time are concatenated with no separator. Either or both sides of `..` may carry an inline date.

| Input | Meaning |
|-------|---------|
| `202609231900..202609232100` | 2026-09-23 19:00 → 2026-09-23 21:00 (system tz) |
| `09231900PT..09232100` | Soonest Sep 23, 19:00 PT → soonest Sep 23, 21:00 system tz |
| `192100..202701200100` | Today 21:00 → 2027-01-20 01:00 (system tz) |
| `1900..202609232100` | Today 19:00 → 2026-09-23 21:00 |

> The parser always peels the last 4 digits (after tz strip) as HHMM, and everything before as the date prefix.

---

## Defaults

| Parameter | Default | Configurable in TOML |
|-----------|---------|----------------------|
| Event name | Required first positional argument | No |
| Date | Today (system date) | No |
| Timezone | System timezone | No |
| Duration (end omitted) | 60 minutes | Yes |
| Notification | None | No |
| Open browser after creation | `true` | Yes |
| Calendar | `"primary"` | Yes |

---

## Conflict & Error Rules

| Condition | Behaviour |
|-----------|-----------|
| `-tz` supplied AND inline tz on either time | **Error**: ambiguous timezone — remove one |
| `-day` supplied AND inline date in either time | **Error**: ambiguous date — remove one |
| Start ≥ end (after resolving to absolute UTC) | **Error**: invalid range |
| Unrecognised timezone abbreviation | **Error**: lists supported values from TOML |
| Time token is not exactly 4 digits (after tz strip) | **Error**: must be 4-digit HHMM |
| Date portion digit count is not 2, 4, 6, or 8 | **Error**: unrecognised date format |

---

## Dry Run

```bash
event "Test Event" 1900..2100 -dr
```

Prints the fully-formed `gws` command to stdout. Does **not** submit. Does **not** open browser.

Example output:
```
[dry-run] gws calendar events insert --params '{"calendarId": "primary"}' --json '{
  "summary": "Test Event",
  "start": { "dateTime": "2026-08-20T19:00:00-06:00", "timeZone": "America/Denver" },
  "end":   { "dateTime": "2026-08-20T21:00:00-06:00", "timeZone": "America/Denver" }
}'
```

---

## Notification

```bash
event Standup 1900.. -notif 10
```

Sets a single popup reminder 10 minutes before the event. Adds to the `gws` JSON body:

```json
"reminders": {
  "useDefault": false,
  "overrides": [{ "method": "popup", "minutes": 10 }]
}
```

---

## Browser Behaviour

After a successful creation, the script opens the Google Calendar event edit page (`htmlLink` from the `gws` response) in the default browser, so you can review and fine-tune details.

- Default: **on**
- `-dr` always suppresses it
- Change default: set `open_browser = false` in `event.toml`

Platform open commands used: `xdg-open` (Linux), `open` (macOS), `Start-Process` (Windows).

---

## Output

After creation, always printed to terminal:

```
✓ Event created: "Gaming"
  Start:    2026-08-21 17:45 MT
  End:      2026-08-21 21:00 MT
  Link:     https://www.google.com/calendar/event?eid=...
  Event ID: 3640kifss2i1dqb5bqlo5pe3dg
```

---

## TOML Config

Located at `~/.config/event/event.toml` (or path in `EVENT_CONFIG` env var).

```toml
[defaults]
duration_mins = 60
open_browser  = true
calendar_id   = "primary"

[flags]
day          = ["-day", "--day"]
location     = ["-l", "--location"]
description  = ["-des", "--description"]
timezone     = ["-tz", "--timezone"]
notification = ["-notif", "--notification"]
dry_run      = ["-dr", "--dry-run"]

[timezones]
ET  = "America/New_York"
CT  = "America/Chicago"
MT  = "America/Denver"
PT  = "America/Los_Angeles"
UTC = "UTC"
GMT = "UTC"
```

> To rename a flag, edit its list under `[flags]`. The first entry is the short form, second is the long form. No flag names are hardcoded in the source.
