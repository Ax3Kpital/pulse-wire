#!/usr/bin/env bash
# Manage the LaunchAgent that starts the backend at login.
# Usage: scripts/launchagent.sh install|uninstall|start|stop|status
source "$(dirname "$0")/_env.sh"
LABEL="com.local.news-aggregator"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

case "${1:-status}" in
  install)
    mkdir -p "$HOME/Library/LaunchAgents" "$REPO/logs"
    sed "s|__REPO__|$REPO|g" "$REPO/launchd/$LABEL.plist.template" > "$PLIST"
    plutil -lint "$PLIST"
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "Installed and started. It will also start automatically at login."
    ;;
  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Uninstalled (it will no longer start at login)."
    ;;
  start)   launchctl kickstart -k "$DOMAIN/$LABEL" && echo "Started." ;;
  stop)    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null && echo "Stopped until next login (or 'install')." || echo "Not running." ;;
  status)  launchctl print "$DOMAIN/$LABEL" 2>/dev/null | grep -E "state|pid|last exit" || echo "Not loaded." ;;
  *) echo "usage: $0 install|uninstall|start|stop|status" >&2; exit 2 ;;
esac
