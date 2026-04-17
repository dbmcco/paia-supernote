#!/usr/bin/env bash
set -euo pipefail

INGEST_LABEL="com.paia.supernote.ingest"
ENRICH_LABEL="com.paia.supernote.enrich"
PLIST_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/paia-supernote"

case "${1:-help}" in
  install)
    mkdir -p "$PLIST_DIR" "$LOG_DIR"
    cp "$(dirname "$0")/paia-supernote-ingest.plist" "$PLIST_DIR/$INGEST_LABEL.plist"
    cp "$(dirname "$0")/paia-supernote-enrich.plist" "$PLIST_DIR/$ENRICH_LABEL.plist"
    launchctl load "$PLIST_DIR/$INGEST_LABEL.plist"
    launchctl load "$PLIST_DIR/$ENRICH_LABEL.plist"
    ;;
  uninstall)
    launchctl unload "$PLIST_DIR/$INGEST_LABEL.plist" 2>/dev/null || true
    launchctl unload "$PLIST_DIR/$ENRICH_LABEL.plist" 2>/dev/null || true
    rm -f "$PLIST_DIR/$INGEST_LABEL.plist" "$PLIST_DIR/$ENRICH_LABEL.plist"
    ;;
  start)
    launchctl start "$INGEST_LABEL"
    launchctl start "$ENRICH_LABEL"
    ;;
  stop)
    launchctl stop "$INGEST_LABEL"
    launchctl stop "$ENRICH_LABEL"
    ;;
  status)
    launchctl list "$INGEST_LABEL" || true
    launchctl list "$ENRICH_LABEL" || true
    ;;
  logs)
    tail -f "$LOG_DIR/ingest.stdout.log" "$LOG_DIR/ingest.stderr.log" \
            "$LOG_DIR/enrich.stdout.log" "$LOG_DIR/enrich.stderr.log"
    ;;
  *)
    echo "Usage: $0 {install|uninstall|start|stop|status|logs}"
    exit 1
    ;;
esac
