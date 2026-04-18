# CLAUDE.md

Context for Claude Code working in this repo. Read this before making changes.

## Repo purpose

Frank's personal automation scripts, kept in git so a laptop swap is just `clone && ./setup.sh`. Currently one automation: a 6 AM daily briefing delivered via iMessage.

## Layout

```
personal-automations/
├── README.md
├── setup.sh                            # one-time install: Keychain + launchd
└── daily_briefing/
    ├── briefing.py                     # main script
    ├── system_prompt.md                # LLM instructions — edit to tune output
    ├── com.frank.dailybriefing.plist   # launchd template (paths substituted by setup.sh)
    └── briefing.log                    # gitignored
```

## How the briefing works

1. Reads today's events from the macOS Calendar SQLite DB at `~/Library/Group Containers/group.com.apple.calendar/Calendar.sqlitedb` for three calendars: `Calendar` (personal iCloud), `Family` (shared), `frank.reno@procore.com` (work).
2. Reads reminders via AppleScript from the `Family`, `Work`, and `Reminders` lists (due today or overdue).
3. Fetches weather from `wttr.in` for Littleton, CO.
4. Sends the raw JSON to Claude Haiku (`claude-haiku-4-5-20251001`) with `system_prompt.md` as the system prompt.
5. Delivers the response via iMessage to `+17202989368`.

## Key identities and rules

- **Bosses to flag:** Michael Marfise, Abe Fathman (matched by name or email substrings `marfise`, `fathman`).
- **External meetings:** any attendee whose email is not `@procore.com`.
- **Skip on weekends:** work section is omitted entirely (no "no work events today").
- **Fridays:** narrative should note if the day looks light, or flag if it doesn't.
- **Utility blocks to skip** (unless an accepted meeting is inside the window): lunch, focus time, ask-before-scheduling, no-meeting holds, school pickup. Patterns live in `SKIP_PATTERNS` in `briefing.py`.
- **Weather location:** Littleton, CO (set in `WEATHER_LOCATION`).

## Tuning vs. coding

- **Tone, format, filtering emphasis → edit `system_prompt.md`.** No code change needed.
- **Data collection, new sources, bug fixes → edit `briefing.py`.**

## Test and commit

```bash
python3 daily_briefing/briefing.py          # runs the full pipeline (sends an iMessage)
git add . && git commit -m "..." && git push
```

The script sends a real iMessage when run — keep that in mind before testing.

## Secrets

- Anthropic API key is stored in the macOS Keychain under service `claude_api_key`. Retrieved via `security find-generic-password`. Never hardcode it, never commit it, never print it.

## Known gotchas

- **SSL certs:** `ssl_context()` in `briefing.py` tries `certifi` first, then a subprocess-located cert bundle, then falls back to unverified as last resort. Don't remove the fallback chain without testing on a fresh macOS install.
- **AppleScript timeout:** the Reminders fetch can hang if the Reminders app is slow — treat missing reminders as non-fatal.
- **Attendee lookup:** `get_attendees()` tries `Participant`, `Attendee`, `EventAttendee`, then `Identity`. Apple changes these table names between macOS versions. Gracefully returns `[]` if none match — do not hard-fail on schema misses.

## Don't do these without asking

- Change the launchd schedule (6:00 AM is intentional).
- Change the iMessage target number or API model without confirmation.
- Add dependencies that aren't in the macOS system Python — the launchd job runs `/usr/bin/python3` with no venv.
- Commit anything matching `.log`, `*.pyc`, or `__pycache__/` (already gitignored).
