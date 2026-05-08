#!/usr/bin/env bash
set -euo pipefail

SERVICE_LABEL="com.paia.supernote.service"
INGEST_LABEL="com.paia.supernote.ingest"
ENRICH_LABEL="com.paia.supernote.enrich"
PLIST_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/paia-supernote"
LAUNCH_DOMAIN="gui/$(id -u)"

case "${1:-help}" in
  install)
    mkdir -p "$PLIST_DIR" "$LOG_DIR"
    cp "$(dirname "$0")/paia-supernote-service.plist" "$PLIST_DIR/$SERVICE_LABEL.plist"
    cp "$(dirname "$0")/paia-supernote-ingest.plist" "$PLIST_DIR/$INGEST_LABEL.plist"
    cp "$(dirname "$0")/paia-supernote-enrich.plist" "$PLIST_DIR/$ENRICH_LABEL.plist"
    launchctl load "$PLIST_DIR/$SERVICE_LABEL.plist"
    launchctl load "$PLIST_DIR/$INGEST_LABEL.plist"
    launchctl load "$PLIST_DIR/$ENRICH_LABEL.plist"
    ;;
  uninstall)
    launchctl unload "$PLIST_DIR/$SERVICE_LABEL.plist" 2>/dev/null || true
    launchctl unload "$PLIST_DIR/$INGEST_LABEL.plist" 2>/dev/null || true
    launchctl unload "$PLIST_DIR/$ENRICH_LABEL.plist" 2>/dev/null || true
    rm -f "$PLIST_DIR/$SERVICE_LABEL.plist" "$PLIST_DIR/$INGEST_LABEL.plist" "$PLIST_DIR/$ENRICH_LABEL.plist"
    ;;
  start)
    launchctl start "$SERVICE_LABEL"
    launchctl start "$INGEST_LABEL"
    launchctl start "$ENRICH_LABEL"
    ;;
  stop)
    launchctl stop "$SERVICE_LABEL"
    launchctl stop "$INGEST_LABEL"
    launchctl stop "$ENRICH_LABEL"
    ;;
  status)
    launchctl print "$LAUNCH_DOMAIN/$SERVICE_LABEL" || true
    launchctl print "$LAUNCH_DOMAIN/$INGEST_LABEL" || true
    launchctl print "$LAUNCH_DOMAIN/$ENRICH_LABEL" || true
    ;;
  logs)
    tail -f "$LOG_DIR/service.stdout.log" "$LOG_DIR/service.stderr.log" \
            "$LOG_DIR/ingest.stdout.log" "$LOG_DIR/ingest.stderr.log" \
            "$LOG_DIR/enrich.stdout.log" "$LOG_DIR/enrich.stderr.log"
    ;;
  *)
    echo "Usage: $0 {install|uninstall|start|stop|status|logs}"
    exit 1
    ;;
esac
