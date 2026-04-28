#!/usr/bin/env python3
"""
Daily Briefing Script
Collects calendar, reminders, and weather data, generates a narrative
briefing via Claude API, and delivers it via iMessage.
"""

import subprocess
import sqlite3
import json
import urllib.request
import urllib.error
import ssl
import logging
import traceback
from logging.handlers import RotatingFileHandler
from datetime import datetime
import os
import sys
from pathlib import Path

# ── Logging setup ─────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
LOG_PATH = SCRIPT_DIR / "briefing.log"

logger = logging.getLogger("briefing")
logger.setLevel(logging.INFO)
_handler = RotatingFileHandler(LOG_PATH, maxBytes=512_000, backupCount=3)
_handler.setFormatter(logging.Formatter(
    "[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
logger.addHandler(_handler)
# Also stream to stderr so launchd's StandardErrorPath catches it as a backup
_stream = logging.StreamHandler(sys.stderr)
_stream.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
logger.addHandler(_stream)


def ssl_context():
    """Return an SSL context that works on macOS regardless of cert install state."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    try:
        pem = subprocess.run(
            ["python3", "-c", "import certifi; print(certifi.where())"],
            capture_output=True, text=True
        ).stdout.strip()
        if pem:
            return ssl.create_default_context(cafile=pem)
    except Exception:
        pass
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ── Config ────────────────────────────────────────────────────────────────────
IMESSAGE_TARGET = "+17202989368"
CALENDAR_DB = os.path.expanduser(
    "~/Library/Group Containers/group.com.apple.calendar/Calendar.sqlitedb"
)
SYSTEM_PROMPT_PATH = SCRIPT_DIR / "system_prompt.md"
WEATHER_LOCATION = "Littleton,CO"
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

WORK_CALENDAR = "frank.reno@procore.com"
PERSONAL_CALENDARS = {"Calendar", "Family"}
ALL_CALENDARS = {WORK_CALENDAR} | PERSONAL_CALENDARS

SKIP_PATTERNS = [
    "lunch", "ask before scheduling", "focus time", "no meeting",
    "⛔", "hold", "blocked", "school pickup", "pickup",
]

APPLE_EPOCH = 978307200  # seconds between Unix epoch and Apple's Jan 1 2001
REMINDERS_TIMEOUT = 45   # seconds — Family list (~600 items) takes ~11s alone via AppleScript
IMESSAGE_TIMEOUT = 15    # seconds — Messages send is fast


# ── Safe fetch wrapper ────────────────────────────────────────────────────────
def safe_fetch(name, fn, *args, **kwargs):
    """Run a fetcher; return (data, error_string_or_None). Logs on failure."""
    try:
        result = fn(*args, **kwargs)
        logger.info(f"{name}: OK")
        return result, None
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        logger.error(f"{name} failed: {err}")
        logger.error(traceback.format_exc())
        return None, f"{name}: {err}"


# ── Keychain ──────────────────────────────────────────────────────────────────
def get_api_key():
    result = subprocess.run(
        ["security", "find-generic-password",
         "-a", "claude_api_key", "-s", "claude_api_key", "-w"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Claude API key not found in Keychain. "
            f"stderr: {result.stderr.strip() or '(empty)'}"
        )
    return result.stdout.strip()


# ── Weather ───────────────────────────────────────────────────────────────────
def get_weather():
    """Fetch weather from wttr.in. Raises on failure (caught by safe_fetch)."""
    url = f"https://wttr.in/{WEATHER_LOCATION}?format=j1"
    req = urllib.request.Request(url, headers={"User-Agent": "curl/7.0"})
    with urllib.request.urlopen(req, timeout=10, context=ssl_context()) as resp:
        data = json.loads(resp.read())
    current = data["current_condition"][0]
    today_wx = data["weather"][0]
    hourly_midday = today_wx["hourly"][4]
    return {
        "temp_f": current["temp_F"],
        "feels_like_f": current["FeelsLikeF"],
        "desc": current["weatherDesc"][0]["value"],
        "high_f": today_wx["maxtempF"],
        "low_f": today_wx["mintempF"],
        "precip_chance_pct": hourly_midday.get("chanceofrain", "0"),
        "snow_chance_pct": hourly_midday.get("chanceofsnow", "0"),
    }


# ── Calendar ──────────────────────────────────────────────────────────────────
def apple_ts_to_dt(ts):
    return datetime.fromtimestamp(float(ts) + APPLE_EPOCH)


def is_utility_block(summary):
    if not summary:
        return True
    s = summary.lower()
    return any(p in s for p in SKIP_PATTERNS)


def get_attendees(conn, item_rowid):
    """Best-effort attendee lookup — gracefully returns [] if table missing."""
    candidate_tables = ["Participant", "Attendee", "EventAttendee"]
    for table in candidate_tables:
        try:
            rows = conn.execute(
                f"SELECT email, common_name FROM {table} WHERE calendar_item_id = ?",
                (item_rowid,)
            ).fetchall()
            return [{"email": r[0] or "", "name": r[1] or ""} for r in rows]
        except sqlite3.OperationalError:
            continue
    try:
        rows = conn.execute("""
            SELECT i.email, i.display_name
            FROM Identity i
            WHERE i.calendar_item_id = ?
        """, (item_rowid,)).fetchall()
        return [{"email": r[0] or "", "name": r[1] or ""} for r in rows]
    except Exception:
        return []


def get_calendar_events(target_date):
    if not os.path.exists(CALENDAR_DB):
        raise FileNotFoundError(f"Calendar.sqlitedb not found at {CALENDAR_DB}")

    try:
        conn = sqlite3.connect(CALENDAR_DB)
    except sqlite3.OperationalError as e:
        # Most common cause: Full Disk Access not granted to the running Python binary
        raise PermissionError(
            f"Cannot open Calendar.sqlitedb ({e}). "
            f"Likely missing Full Disk Access for {sys.executable}."
        )

    conn.row_factory = sqlite3.Row

    start_unix = datetime(
        target_date.year, target_date.month, target_date.day
    ).timestamp()
    end_unix = start_unix + 86400
    start_a = start_unix - APPLE_EPOCH
    end_a = end_unix - APPLE_EPOCH

    placeholders = ",".join("?" * len(ALL_CALENDARS))
    query = f"""
        SELECT
            ci.ROWID,
            ci.summary,
            ci.start_date,
            ci.end_date,
            ci.all_day,
            ci.has_attendees,
            ci.invitation_status,
            ci.description,
            ci.conference_url,
            c.title AS calendar_name
        FROM CalendarItem ci
        JOIN Calendar c ON ci.calendar_id = c.ROWID
        WHERE ci.start_date >= ?
          AND ci.start_date < ?
          AND ci.hidden = 0
          AND c.title IN ({placeholders})
        ORDER BY ci.start_date
    """

    rows = conn.execute(query, [start_a, end_a] + list(ALL_CALENDARS)).fetchall()

    events = []
    for row in rows:
        summary = row["summary"] or "(no title)"
        start_dt = apple_ts_to_dt(row["start_date"])
        end_dt = apple_ts_to_dt(row["end_date"])

        attendees = []
        if row["has_attendees"]:
            attendees = get_attendees(conn, row["ROWID"])

        boss_names = {"michael marfise", "abe fathman"}
        has_boss = any(
            a["name"].lower() in boss_names or
            any(b in a["email"].lower() for b in ["marfise", "fathman"])
            for a in attendees
        )
        external_attendees = [
            a for a in attendees
            if a["email"] and "@procore.com" not in a["email"].lower()
        ]

        events.append({
            "rowid": row["ROWID"],
            "summary": summary,
            "start": start_dt.strftime("%-I:%M %p"),
            "end": end_dt.strftime("%-I:%M %p"),
            "start_minutes": int(start_dt.hour * 60 + start_dt.minute),
            "end_minutes": int(end_dt.hour * 60 + end_dt.minute),
            "all_day": bool(row["all_day"]),
            "calendar": row["calendar_name"],
            "is_work": row["calendar_name"] == WORK_CALENDAR,
            "utility_block": is_utility_block(summary),
            "has_attendees": bool(row["has_attendees"]),
            "has_boss": has_boss,
            "external_attendees": [a["email"] or a["name"] for a in external_attendees],
            "has_video": bool(row["conference_url"]),
            "invitation_status": row["invitation_status"],
            "description": (row["description"] or "")[:150],
        })

    conn.close()
    return events


# ── Reminders ─────────────────────────────────────────────────────────────────
def get_reminders():
    script = '''
tell application "Reminders"
    set output to ""
    set targetLists to {"Family", "Work", "Reminders"}
    set today to current date
    set todayStart to today - (time of today)
    set tomorrow to todayStart + (1 * days)
    repeat with listName in targetLists
        try
            set rl to list listName
            set rems to (every reminder of rl whose completed is false)
            repeat with r in rems
                try
                    set dd to due date of r
                    if dd < tomorrow then
                        set overdue to ""
                        if dd < todayStart then set overdue to "OVERDUE"
                        set output to output & listName & "|" & (name of r) & "|" & overdue & linefeed
                    end if
                on error
                    -- no due date, skip
                end try
            end repeat
        end try
    end repeat
    return output
end tell
'''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True,
            timeout=REMINDERS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"Reminders AppleScript timed out after {REMINDERS_TIMEOUT}s — "
            f"either missing Automation permission for {sys.executable} → Reminders, "
            f"or the Family list has grown so large the 'whose' filter is exceeding the budget."
        )

    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "(no output)"
        raise RuntimeError(
            f"Reminders osascript failed (rc={result.returncode}): {stderr}"
        )

    reminders = []
    if result.stdout.strip():
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("|")
            if len(parts) >= 2:
                reminders.append({
                    "list": parts[0],
                    "title": parts[1],
                    "overdue": len(parts) > 2 and parts[2] == "OVERDUE",
                })
    return reminders


# ── Claude API ────────────────────────────────────────────────────────────────
def call_claude(api_key, system_prompt, user_message):
    payload = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": 1000,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_message}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl_context()) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        raise RuntimeError(f"Claude API HTTP {e.code}: {body}")
    return data["content"][0]["text"]


# ── iMessage ──────────────────────────────────────────────────────────────────
def send_imessage(message, target):
    escaped = message.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    script = f'''
tell application "Messages"
    set targetService to 1st service whose service type = iMessage
    set targetBuddy to buddy "{target}" of targetService
    send "{escaped}" to targetBuddy
end tell
'''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True,
            timeout=IMESSAGE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"iMessage send timed out after {IMESSAGE_TIMEOUT}s — "
            f"likely missing Automation permission for {sys.executable} → Messages."
        )
    if result.returncode != 0:
        stderr = result.stderr.strip() or "(no stderr)"
        raise RuntimeError(f"iMessage send failed (rc={result.returncode}): {stderr}")


# ── Main ──────────────────────────────────────────────────────────────────────
def run_briefing():
    now = datetime.now()
    is_weekend = now.weekday() >= 5

    logger.info(f"Starting daily briefing for {now.strftime('%Y-%m-%d %H:%M')}")
    logger.info(f"Python: {sys.executable}")

    # Fetch each source independently — partial failure is OK
    weather, wx_err = safe_fetch("weather", get_weather)
    events, cal_err = safe_fetch("calendar", get_calendar_events, now)
    reminders, rem_err = safe_fetch("reminders", get_reminders)

    errors = [e for e in (wx_err, cal_err, rem_err) if e]

    # If literally everything failed, abort — no point hitting the LLM
    if weather is None and events is None and reminders is None:
        raise RuntimeError(
            "All data sources failed. Errors: " + " | ".join(errors)
        )

    system_prompt = SYSTEM_PROMPT_PATH.read_text()
    context = {
        "date": now.strftime("%A, %B %-d, %Y"),
        "is_weekend": is_weekend,
        "weather": weather if weather is not None else {"unavailable": wx_err},
        "events": events if events is not None else {"unavailable": cal_err},
        "reminders": reminders if reminders is not None else {"unavailable": rem_err},
        "data_source_errors": errors,
    }

    user_message = (
        "Here is today's data. Generate my morning briefing.\n"
        "If any data sources are marked unavailable or appear in "
        "data_source_errors, briefly mention what's missing rather than "
        "fabricating it.\n\n"
        + json.dumps(context, indent=2, default=str)
    )

    api_key = get_api_key()
    briefing = call_claude(api_key, system_prompt, user_message)

    logger.info(f"Briefing generated ({len(briefing)} chars)")
    send_imessage(briefing, IMESSAGE_TARGET)
    logger.info("Briefing sent successfully.")


def main():
    try:
        run_briefing()
    except Exception as e:
        err_type = type(e).__name__
        logger.error(f"FATAL {err_type}: {e}")
        logger.error(traceback.format_exc())
        # Send the actual error type and message — never just "authorization denied"
        try:
            send_imessage(
                f"⚠️ Daily briefing FATAL\n{err_type}: {str(e)[:300]}",
                IMESSAGE_TARGET,
            )
        except Exception as send_err:
            logger.error(f"Could not send error iMessage either: {send_err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
