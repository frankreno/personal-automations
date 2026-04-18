# personal-automations

Frank's personal automation scripts. Stored here so if the laptop dies, clone and run `setup.sh`.

---

## Setup (new machine)

```bash
git clone <this-repo>
cd personal-automations
chmod +x setup.sh
./setup.sh
```

That's it. Script prompts for your Anthropic API key (stored in Keychain, never committed) and installs the launchd scheduler.

---

## Daily Briefing

Morning briefing delivered via iMessage at **6:00 AM every day**.

**What it covers:**
- Opening narrative — what kind of day is it?
- Weather (Parker, CO)
- Work: boss meetings (Michael Marfise, Abe Fathman), external attendees, conflicts, pacing — weekdays only, skipped entirely on weekends
- Personal/family events worth knowing
- Reminders due today + overdue (Family, Work, Reminders lists)

**What it skips:**
- Utility blocks (lunch holds, focus time, ask-before-scheduling)
- Kid school pickup (unless it conflicts with work)
- Empty sections

**Files:**
```
daily_briefing/
├── briefing.py          ← main script
├── system_prompt.md     ← Frank's values/context for the LLM (edit this to tune the output)
├── com.frank.dailybriefing.plist  ← launchd scheduler template
└── briefing.log         ← gitignored, check here if something breaks
```

**Tune the output:** Edit `system_prompt.md` — no code changes needed.

**Test manually:**
```bash
python3 daily_briefing/briefing.py
```

**Check logs:**
```bash
tail -f daily_briefing/briefing.log
```

**Uninstall:**
```bash
launchctl unload ~/Library/LaunchAgents/com.frank.dailybriefing.plist
rm ~/Library/LaunchAgents/com.frank.dailybriefing.plist
```

**Cost:** ~$1.50/year (Claude Haiku 4.5)

---

## Future automations

Add new scripts here as you build them. Each gets its own folder with a `README` section above.

---

## Notes

- API key lives in macOS Keychain under service `claude_api_key` — never in this repo
- launchd fires at 6am local time regardless of login state (Mac must be awake or set to wake)
- Attendee-based filtering (boss detection, external meeting flagging) requires the Calendar SQLite `Participant` table — gracefully degrades if not present
