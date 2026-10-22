#!/bin/bash
# Nainstaluje bota jako LaunchAgent (spustí se po přihlášení k Macu).
#   ./deploy/install_launchd.sh install     # zapnout
#   ./deploy/install_launchd.sh uninstall   # vypnout
set -e
BOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/cz.bolt.liftago-bot.plist"
case "$1" in
  install)
    mkdir -p "$BOT_DIR/state" "$HOME/Library/LaunchAgents"
    sed "s#__BOT_DIR__#$BOT_DIR#g" "$BOT_DIR/deploy/cz.bolt.liftago-bot.plist" > "$PLIST"
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    echo "Hotovo. Log: $BOT_DIR/state/bot.log"
    ;;
  uninstall)
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Odinstalováno."
    ;;
  *) echo "Použití: $0 install|uninstall"; exit 1 ;;
esac
