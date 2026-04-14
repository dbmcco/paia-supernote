"""
ABOUTME: User board - persistent conversation surface for braydon
Author: Braydon McCormick <braydon@braydondm.com>
Purpose: CLI interface for interacting with paia-supernote service
"""

import asyncio
import json
import time
from datetime import datetime
from typing import Dict, Any, Optional
from pathlib import Path

import httpx
import structlog

from .events import EventsClient

log = structlog.get_logger(__name__)


class UserBoard:
    """CLI-based user board for paia-supernote service interaction."""

    def __init__(self):
        """Initialize user board."""
        self.events = EventsClient()
        self.session_file = Path("~/.paia-supernote-session.json").expanduser()
        self.session_data = self._load_session()

    def _load_session(self) -> Dict[str, Any]:
        """Load persistent session data."""
        if self.session_file.exists():
            try:
                with open(self.session_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"last_seen": 0, "preferences": {}}

    def _save_session(self) -> None:
        """Save session data."""
        try:
            with open(self.session_file, 'w') as f:
                json.dump(self.session_data, f, indent=2)
        except Exception as e:
            log.error("failed_to_save_session", error=str(e))

    async def start(self) -> None:
        """Start interactive user board session."""
        print("🎯 PAIA Supernote User Board")
        print("=" * 40)

        try:
            await self.events.start()
            print("✅ Connected to paia-events")
        except Exception as e:
            print(f"⚠️  Failed to connect to paia-events: {e}")
            print("   Starting in offline mode...")

        await self._main_loop()

    async def stop(self) -> None:
        """Stop user board."""
        await self.events.stop()
        self._save_session()
        print("👋 Session ended")

    async def _main_loop(self) -> None:
        """Main interactive loop."""
        while True:
            try:
                await self._show_menu()
                choice = input("\n> ").strip().lower()

                if choice in ['q', 'quit', 'exit']:
                    break
                elif choice in ['s', 'status']:
                    await self._show_status()
                elif choice in ['e', 'events']:
                    await self._show_recent_events()
                elif choice in ['w', 'write']:
                    await self._send_write_request()
                elif choice in ['r', 'refresh']:
                    await self._refresh_data()
                elif choice in ['h', 'help']:
                    self._show_help()
                else:
                    print("❌ Unknown command. Type 'h' for help.")

            except KeyboardInterrupt:
                print("\n👋 Goodbye!")
                break
            except Exception as e:
                print(f"❌ Error: {e}")

    async def _show_menu(self) -> None:
        """Show main menu."""
        print("\n" + "─" * 40)
        print("📋 Commands:")
        print("  s) Status     - Service & sync status")
        print("  e) Events     - Recent transcriptions & events")
        print("  w) Write      - Send content to Supernote")
        print("  r) Refresh    - Refresh data")
        print("  h) Help       - Show detailed help")
        print("  q) Quit       - Exit user board")

    async def _show_status(self) -> None:
        """Show service and sync status."""
        print("\n🔍 Service Status")
        print("─" * 20)

        # Check paia-supernote service (if running)
        try:
            # Try to connect to events to see if service is up
            async with httpx.AsyncClient() as client:
                response = await client.get("http://localhost:3511/v1/health", timeout=2)
                if response.status_code == 200:
                    print("✅ paia-events service: running")
                else:
                    print("⚠️  paia-events service: degraded")
        except Exception:
            print("❌ paia-events service: not accessible")

        # Check Supernote sync folder
        supernote_dir = Path("~/Supernote").expanduser()
        if supernote_dir.exists():
            note_files = list(supernote_dir.glob("*.note"))
            print(f"📁 Supernote sync: {len(note_files)} notebooks found")

            # Show recent activity
            recent_files = sorted(note_files, key=lambda f: f.stat().st_mtime, reverse=True)[:3]
            if recent_files:
                print("   Recent activity:")
                for file in recent_files:
                    mtime = datetime.fromtimestamp(file.stat().st_mtime)
                    print(f"     • {file.stem} (modified {mtime.strftime('%H:%M')})")
        else:
            print("❌ Supernote sync folder not found")

    async def _show_recent_events(self) -> None:
        """Show recent events and transcriptions."""
        print("\n📝 Recent Events")
        print("─" * 20)

        try:
            # Get recent events from paia-events
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"http://localhost:3511/v1/subscribers/paia-supernote/events",
                    params={"since": self.session_data["last_seen"], "limit": 10},
                    timeout=5
                )

                if response.status_code == 200:
                    data = response.json()
                    events = data.get("events", [])

                    if not events:
                        print("📭 No new events")
                        return

                    for event in events[-10:]:  # Show last 10 events
                        payload = event.get("payload", {})
                        event_time = datetime.fromtimestamp(event.get("timestamp", time.time()))

                        if "notebook" in payload:
                            notebook = payload["notebook"]
                            page = payload.get("page", "?")

                            if "text" in payload:
                                text_preview = payload["text"][:60] + ("..." if len(payload["text"]) > 60 else "")
                                print(f"📄 {notebook}:{page} - {text_preview}")
                                print(f"    {event_time.strftime('%H:%M:%S')}")

                        # Update last seen
                        if event["id"] > self.session_data["last_seen"]:
                            self.session_data["last_seen"] = event["id"]

                    self._save_session()
                else:
                    print("❌ Failed to fetch events")

        except Exception as e:
            print(f"❌ Error fetching events: {e}")

    async def _send_write_request(self) -> None:
        """Send a write request to Supernote."""
        print("\n✏️  Write to Supernote")
        print("─" * 20)

        notebook = input("📒 Notebook name: ").strip()
        if not notebook:
            print("❌ Notebook name required")
            return

        print("📝 Content types:")
        print("  1) note    - Regular text note")
        print("  2) summary - Summary/report")
        print("  3) task    - Task list")

        content_type_map = {"1": "note", "2": "summary", "3": "task"}
        choice = input("Content type (1-3): ").strip()
        content_type = content_type_map.get(choice, "note")

        print(f"\n✏️  Enter your {content_type} (press Enter twice to finish):")
        content_lines = []
        empty_count = 0

        while empty_count < 2:
            line = input()
            if line.strip():
                content_lines.append(line)
                empty_count = 0
            else:
                empty_count += 1
                if empty_count == 1:
                    content_lines.append("")

        content = "\n".join(content_lines).strip()
        if not content:
            print("❌ No content provided")
            return

        # Send write request via events
        try:
            await self.events._publish(
                event_type="supernote.write_request",
                payload={
                    "agent": "braydon",
                    "notebook": notebook,
                    "content_type": content_type,
                    "content": content,
                    "timestamp": time.time()
                }
            )
            print(f"✅ Write request sent to {notebook}")
        except Exception as e:
            print(f"❌ Failed to send write request: {e}")

    async def _refresh_data(self) -> None:
        """Refresh all data."""
        print("\n🔄 Refreshing...")
        await self._show_status()
        print("✅ Data refreshed")

    def _show_help(self) -> None:
        """Show detailed help."""
        print("\n📖 PAIA Supernote User Board Help")
        print("=" * 40)
        print("""
This user board provides a command-line interface for interacting with the
PAIA Supernote service. Through this interface you can:

🔍 STATUS: Check if services are running and see Supernote sync activity
📝 EVENTS: View recent note transcriptions and system events
✏️  WRITE: Send content from agents back to your Supernote
🔄 REFRESH: Update status and event information

The board maintains session state in ~/.paia-supernote-session.json to
track which events you've already seen.

Commands:
  s, status  - Show service status and Supernote sync information
  e, events  - Display recent transcriptions and events
  w, write   - Compose and send content to a Supernote notebook
  r, refresh - Refresh all status information
  h, help    - Show this help message
  q, quit    - Exit the user board

Tips:
• The board connects to paia-events on localhost:3511
• Supernote files are expected in ~/Supernote/
• Write requests are sent as events for the main service to process
• All timestamps are shown in local time
        """)


async def main():
    """Main entry point for user board."""
    board = UserBoard()
    try:
        await board.start()
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    finally:
        await board.stop()


if __name__ == "__main__":
    asyncio.run(main())