#!/bin/bash
# setup.sh — one-time setup for personal automations
# Run this on a new machine after cloning the repo.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRIEFING_DIR="$SCRIPT_DIR/daily_briefing"
PLIST_NAME="com.frank.dailybriefing"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
LOG_PATH="$BRIEFING_DIR/briefing.log"
SCRIPT_PATH="$BRIEFING_DIR/briefing.py"
PLIST_SRC="$BRIEFING_DIR/$PLIST_NAME.plist"
PLIST_DST="$LAUNCH_AGENTS/$PLIST_NAME.plist"

echo ""
echo "=== Personal Automations Setup ==="
echo ""

# ── Step 1: API Key ───────────────────────────────────────────────────────────
echo "Step 1/2: Store Claude API key in Keychain"
echo "  Get yours at: https://console.anthropic.com/settings/keys"
echo ""
echo -n "  Paste your Anthropic API key (input hidden): "
read -rs API_KEY
echo ""

if [ -z "$API_KEY" ]; then
    echo "  ✗ No key entered. Exiting."
    exit 1
fi

security add-generic-password \
    -a "claude_api_key" \
    -s "claude_api_key" \
    -w "$API_KEY" \
    -U 2>/dev/null && echo "  ✓ API key stored in Keychain"

# ── Step 2: Install launchd job ───────────────────────────────────────────────
echo ""
echo "Step 2/2: Install launchd scheduler (fires at 6:00 AM daily)"

mkdir -p "$LAUNCH_AGENTS"

# Substitute actual paths into the plist
sed \
    -e "s|BRIEFING_SCRIPT_PATH|$SCRIPT_PATH|g" \
    -e "s|BRIEFING_LOG_PATH|$LOG_PATH|g" \
    "$PLIST_SRC" > "$PLIST_DST"

# Unload first in case it's already loaded
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"

echo "  ✓ launchd job installed"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "=== Setup complete ==="
echo ""
echo "  Schedule : 6:00 AM daily (launchd, fires even if screen is locked)"
echo "  Logs     : $LOG_PATH"
echo ""
echo "  Test now : python3 $SCRIPT_PATH"
echo "  Uninstall: launchctl unload $PLIST_DST && rm $PLIST_DST"
echo ""
