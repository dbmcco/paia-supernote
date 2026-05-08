"""
ABOUTME: User board - persistent conversation surface for braydon
Author: Braydon McCormick <braydon@braydondm.com>
Purpose: Enhanced conversational interface for interacting with paia-supernote service
"""

import asyncio
import json
import time
import uuid
import shutil
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List
from pathlib import Path

import httpx
import structlog

from .events import EventsClient

log = structlog.get_logger(__name__)

# Terminal styling constants
class Style:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    ITALIC = '\033[3m'

    # Colors
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    GRAY = '\033[90m'

    # Background colors
    BG_BLUE = '\033[44m'
    BG_GREEN = '\033[42m'
    BG_YELLOW = '\033[43m'
    BG_RED = '\033[41m'

def get_terminal_width() -> int:
    """Get terminal width, fallback to 80."""
    return shutil.get_terminal_size().columns


class UserBoard:
    """Enhanced conversational interface for paia-supernote service interaction."""

    def __init__(self):
        """Initialize user board."""
        self.events = EventsClient()
        self.session_file = Path("~/.paia-supernote-session.json").expanduser()
        self.session_data = self._load_session()
        self.conversation_history: List[Dict[str, Any]] = []
        self.is_connected = False
        self.last_refresh = datetime.now()
        self.stats = {
            "transcriptions_today": 0,
            "write_requests_today": 0,
            "uptime": datetime.now()
        }

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

    def _clear_screen(self) -> None:
        """Clear the terminal screen."""
        print("\033[2J\033[H", end="")

    def _print_header(self) -> None:
        """Print the application header with branding."""
        width = get_terminal_width()

        # PAIA Supernote brand header
        print(f"{Style.CYAN}{Style.BOLD}")
        print("╔" + "═" * (width - 2) + "╗")
        title = "PAIA SUPERNOTE · USER BOARD"
        subtitle = "Persistent Conversation Surface"
        padding = (width - len(title) - 4) // 2
        print(f"║{' ' * padding}{title}{' ' * (width - len(title) - padding - 2)}║")
        padding_sub = (width - len(subtitle) - 4) // 2
        print(f"║{' ' * padding_sub}{Style.DIM}{subtitle}{Style.RESET}{Style.CYAN}{Style.BOLD}{' ' * (width - len(subtitle) - padding_sub - 2)}║")
        print("╚" + "═" * (width - 2) + "╝")
        print(Style.RESET)

    def _print_status_bar(self) -> None:
        """Print the status bar with connection and session info."""
        width = get_terminal_width()

        # Connection status
        connection_icon = "🟢" if self.is_connected else "🔴"
        connection_text = "CONNECTED" if self.is_connected else "OFFLINE"

        # Session uptime
        uptime = datetime.now() - self.stats["uptime"]
        uptime_str = f"{int(uptime.total_seconds() // 60)}m"

        # Create status bar
        left_info = f" {connection_icon} {connection_text}"
        right_info = f"UPTIME {uptime_str} | TRANSCRIPTIONS {self.stats['transcriptions_today']} "

        # Fill middle with spaces
        middle_spaces = width - len(left_info) - len(right_info)
        if middle_spaces < 0:
            middle_spaces = 2

        status_line = f"{Style.BG_BLUE}{Style.WHITE}{left_info}{' ' * middle_spaces}{right_info}{Style.RESET}"
        print(status_line)

    def _print_conversation_prompt(self) -> None:
        """Print the conversation prompt."""
        print(f"\n{Style.GRAY}──────────────────────────────────────────────────────────────────────────────────{Style.RESET}")
        print(f"{Style.BOLD}{Style.WHITE}💬 What would you like to do? {Style.DIM}(type 'help' for commands){Style.RESET}")

    def _add_to_conversation(self, role: str, message: str, context: Dict[str, Any] = None) -> None:
        """Add message to conversation history."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "role": role,  # "user", "system", "assistant"
            "message": message,
            "context": context or {}
        }
        self.conversation_history.append(entry)

        # Keep only last 50 entries to prevent memory bloat
        if len(self.conversation_history) > 50:
            self.conversation_history = self.conversation_history[-50:]

    def _print_recent_conversation(self, limit: int = 5) -> None:
        """Print recent conversation entries."""
        if not self.conversation_history:
            return

        print(f"\n{Style.DIM}Recent Activity:{Style.RESET}")
        recent = self.conversation_history[-limit:]

        for entry in recent:
            timestamp = datetime.fromisoformat(entry["timestamp"])
            time_str = timestamp.strftime("%H:%M")

            role_icon = {
                "user": "👤",
                "system": "⚡",
                "assistant": "🤖"
            }.get(entry["role"], "•")

            role_color = {
                "user": Style.CYAN,
                "system": Style.YELLOW,
                "assistant": Style.GREEN
            }.get(entry["role"], Style.WHITE)

            message = entry["message"][:80] + ("..." if len(entry["message"]) > 80 else "")
            print(f"  {Style.DIM}{time_str}{Style.RESET} {role_icon} {role_color}{message}{Style.RESET}")

    def _print_quick_stats(self) -> None:
        """Print quick stats about today's activity."""
        print(f"\n{Style.BOLD}📊 Today's Activity{Style.RESET}")
        print(f"  📝 Transcriptions: {Style.CYAN}{self.stats['transcriptions_today']}{Style.RESET}")
        print(f"  ✏️  Write Requests: {Style.GREEN}{self.stats['write_requests_today']}{Style.RESET}")

        # Check Supernote sync folder
        supernote_dir = Path("~/Supernote").expanduser()
        if supernote_dir.exists():
            note_files = list(supernote_dir.glob("*.note"))
            print(f"  📁 Notebooks Synced: {Style.MAGENTA}{len(note_files)}{Style.RESET}")

    def _refresh_display(self) -> None:
        """Refresh the entire display."""
        self._clear_screen()
        self._print_header()
        self._print_status_bar()
        self._print_quick_stats()
        self._print_recent_conversation()
        self._print_conversation_prompt()

    async def start(self) -> None:
        """Start interactive user board session."""
        # Initialize connection
        try:
            await self.events.start()
            self.is_connected = True
            self._add_to_conversation("system", "Connected to paia-events service")
        except Exception as e:
            self.is_connected = False
            self._add_to_conversation("system", f"Failed to connect to paia-events: {str(e)}")

        # Initial display
        self._refresh_display()
        self._add_to_conversation("system", "PAIA Supernote User Board session started")

        await self._main_loop()

    async def stop(self) -> None:
        """Stop user board gracefully."""
        self._add_to_conversation("system", "Ending session")
        await self.events.stop()
        self._save_session()
        print(f"\n{Style.BOLD}{Style.CYAN}Thank you for using PAIA Supernote User Board!{Style.RESET}")
        print(f"{Style.DIM}Session data saved. Your conversation history is preserved.{Style.RESET}")
        print(f"{Style.YELLOW}👋 Goodbye!{Style.RESET}\n")

    async def _main_loop(self) -> None:
        """Main conversational loop."""
        refresh_task = None
        try:
            # Start auto-refresh task
            refresh_task = asyncio.create_task(self._auto_refresh_loop())

            while True:
                try:
                    # Get user input
                    user_input = input(f"\n{Style.BOLD}> {Style.RESET}").strip()

                    if not user_input:
                        continue

                    # Add to conversation history
                    self._add_to_conversation("user", user_input)

                    # Parse and handle commands
                    if user_input.lower() in ['quit', 'exit', 'q', 'bye']:
                        self._add_to_conversation("assistant", "Ending session...")
                        break
                    elif user_input.lower() in ['help', 'h', '?']:
                        await self._handle_help()
                    elif user_input.lower() in ['status', 'st']:
                        await self._handle_status()
                    elif user_input.lower() in ['events', 'e']:
                        await self._handle_events()
                    elif user_input.lower().startswith('write'):
                        await self._handle_write_command(user_input)
                    elif user_input.lower() in ['refresh', 'r']:
                        await self._handle_refresh()
                    elif user_input.lower() in ['clear', 'cls']:
                        self._refresh_display()
                        self._add_to_conversation("assistant", "Display cleared and refreshed")
                    else:
                        # Try to interpret as natural language
                        await self._handle_natural_language(user_input)

                except EOFError:
                    # Handle non-interactive environments gracefully
                    print(f"\n\n{Style.YELLOW}⚠️  Non-interactive environment detected.{Style.RESET}")
                    print(f"{Style.DIM}The user board requires an interactive terminal to function properly.{Style.RESET}")
                    print(f"{Style.DIM}Run in a terminal with TTY support for full functionality.{Style.RESET}")
                    self._add_to_conversation("system", "Session ended due to non-interactive environment")
                    break
                except KeyboardInterrupt:
                    print(f"\n\n{Style.YELLOW}👋 Session interrupted. Goodbye!{Style.RESET}")
                    break
                except Exception as e:
                    error_msg = f"Error processing command: {str(e)}"
                    print(f"{Style.RED}❌ {error_msg}{Style.RESET}")
                    self._add_to_conversation("system", error_msg)

        finally:
            if refresh_task:
                refresh_task.cancel()

    async def _auto_refresh_loop(self) -> None:
        """Auto-refresh the display every 30 seconds."""
        while True:
            await asyncio.sleep(30)
            try:
                # Check for new events and update stats
                await self._update_stats()
            except Exception as e:
                log.debug("auto_refresh_error", error=str(e))

    async def _handle_help(self) -> None:
        """Handle help command."""
        help_text = f"""
{Style.BOLD}🔧 Available Commands{Style.RESET}

{Style.CYAN}status{Style.RESET} / {Style.CYAN}st{Style.RESET}     → Check service and sync status
{Style.CYAN}events{Style.RESET} / {Style.CYAN}e{Style.RESET}      → View recent transcriptions
{Style.CYAN}write{Style.RESET}           → Send content to Supernote
{Style.CYAN}refresh{Style.RESET} / {Style.CYAN}r{Style.RESET}     → Refresh display and data
{Style.CYAN}clear{Style.RESET} / {Style.CYAN}cls{Style.RESET}    → Clear and refresh display
{Style.CYAN}help{Style.RESET} / {Style.CYAN}h{Style.RESET}       → Show this help
{Style.CYAN}quit{Style.RESET} / {Style.CYAN}q{Style.RESET}       → Exit user board

{Style.DIM}You can also use natural language like:{Style.RESET}
• "show me recent notes"
• "send a message to notebook Quick"
• "what's the status of my supernote?"

{Style.DIM}The display auto-refreshes every 30 seconds.{Style.RESET}
"""
        print(help_text)
        self._add_to_conversation("assistant", "Displayed help information")

    async def _handle_status(self) -> None:
        """Handle status command with enhanced display."""
        print(f"\n{Style.BOLD}📊 System Status{Style.RESET}")
        print("─" * 50)

        # Check paia-events service
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get("http://localhost:3511/v1/health", timeout=2)
                if response.status_code == 200:
                    print(f"  {Style.GREEN}●{Style.RESET} paia-events service: {Style.BOLD}RUNNING{Style.RESET}")
                    self.is_connected = True
                else:
                    print(f"  {Style.YELLOW}●{Style.RESET} paia-events service: {Style.BOLD}DEGRADED{Style.RESET}")
                    self.is_connected = False
        except Exception as e:
            print(f"  {Style.RED}●{Style.RESET} paia-events service: {Style.BOLD}NOT ACCESSIBLE{Style.RESET}")
            print(f"    {Style.DIM}Error: {str(e)[:60]}{Style.RESET}")
            self.is_connected = False

        # Check Supernote sync folder
        supernote_dir = Path("~/Supernote").expanduser()
        if supernote_dir.exists():
            note_files = list(supernote_dir.glob("*.note"))
            print(f"  {Style.GREEN}●{Style.RESET} Supernote sync: {Style.BOLD}{len(note_files)} notebooks{Style.RESET}")

            # Show recent activity
            if note_files:
                recent_files = sorted(note_files, key=lambda f: f.stat().st_mtime, reverse=True)[:3]
                print(f"\n    {Style.DIM}Recent activity:{Style.RESET}")
                for file in recent_files:
                    mtime = datetime.fromtimestamp(file.stat().st_mtime)
                    age = datetime.now() - mtime
                    if age.days == 0:
                        time_ago = f"{int(age.total_seconds() // 3600)}h ago" if age.total_seconds() > 3600 else f"{int(age.total_seconds() // 60)}m ago"
                    else:
                        time_ago = f"{age.days}d ago"

                    print(f"      {Style.CYAN}📔 {file.stem}{Style.RESET} {Style.DIM}({time_ago}){Style.RESET}")
        else:
            print(f"  {Style.RED}●{Style.RESET} Supernote sync folder: {Style.BOLD}NOT FOUND{Style.RESET}")
            print(f"    {Style.DIM}Expected at: ~/Supernote/{Style.RESET}")

        # Session information
        uptime = datetime.now() - self.stats["uptime"]
        print(f"\n  {Style.MAGENTA}●{Style.RESET} Session uptime: {Style.BOLD}{int(uptime.total_seconds() // 60)}m {int(uptime.total_seconds() % 60)}s{Style.RESET}")

        self._add_to_conversation("assistant", "Displayed detailed system status")

    async def _handle_events(self) -> None:
        """Handle events command with enhanced display."""
        print(f"\n{Style.BOLD}📝 Recent Events & Transcriptions{Style.RESET}")
        print("─" * 50)

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"http://localhost:3511/v1/subscribers/paia-supernote/events",
                    params={"since": self.session_data["last_seen"], "limit": 15},
                    timeout=5
                )

                if response.status_code == 200:
                    data = response.json()
                    events = data.get("events", [])

                    if not events:
                        print(f"  {Style.DIM}📭 No new events since last check{Style.RESET}")
                        print(f"  {Style.DIM}Last updated: {self.last_refresh.strftime('%H:%M:%S')}{Style.RESET}")
                        return

                    # Group events by type
                    transcriptions = []
                    write_requests = []
                    other_events = []

                    for event in events[-15:]:
                        payload = event.get("payload", {})
                        event_type = event.get("event_type", "")

                        if "text" in payload and "notebook" in payload:
                            transcriptions.append(event)
                        elif "write" in event_type.lower():
                            write_requests.append(event)
                        else:
                            other_events.append(event)

                    # Display transcriptions
                    if transcriptions:
                        print(f"\n  {Style.BOLD}{Style.CYAN}📖 Transcriptions{Style.RESET}")
                        for event in transcriptions[-5:]:  # Show last 5
                            payload = event.get("payload", {})
                            event_time = datetime.fromtimestamp(event.get("timestamp", time.time()))
                            notebook = payload.get("notebook", "Unknown")
                            page = payload.get("page", "?")
                            text = payload.get("text", "")

                            text_preview = text[:70] + ("..." if len(text) > 70 else "")
                            time_ago = self._format_time_ago(event_time)

                            print(f"    {Style.GREEN}📔 {notebook}:{page}{Style.RESET} {Style.DIM}({time_ago}){Style.RESET}")
                            print(f"      {Style.DIM}\"{text_preview}\"{Style.RESET}")

                    # Display write requests
                    if write_requests:
                        print(f"\n  {Style.BOLD}{Style.MAGENTA}✏️  Write Requests{Style.RESET}")
                        for event in write_requests[-3:]:
                            payload = event.get("payload", {})
                            event_time = datetime.fromtimestamp(event.get("timestamp", time.time()))
                            agent = payload.get("agent", "Unknown")
                            notebook = payload.get("notebook", "Unknown")
                            time_ago = self._format_time_ago(event_time)

                            print(f"    {Style.YELLOW}🤖 {agent}{Style.RESET} → {Style.CYAN}{notebook}{Style.RESET} {Style.DIM}({time_ago}){Style.RESET}")

                    # Update last seen
                    for event in events:
                        if event["id"] > self.session_data["last_seen"]:
                            self.session_data["last_seen"] = event["id"]

                    self.stats["transcriptions_today"] = len(transcriptions)
                    self.stats["write_requests_today"] = len(write_requests)
                    self._save_session()

                else:
                    print(f"  {Style.RED}❌ Failed to fetch events (HTTP {response.status_code}){Style.RESET}")

        except Exception as e:
            print(f"  {Style.RED}❌ Error fetching events: {str(e)[:60]}{Style.RESET}")

        self._add_to_conversation("assistant", "Displayed recent events and transcriptions")

    def _format_time_ago(self, event_time: datetime) -> str:
        """Format time ago string."""
        now = datetime.now()
        diff = now - event_time

        if diff.days > 0:
            return f"{diff.days}d ago"
        elif diff.seconds > 3600:
            return f"{diff.seconds // 3600}h ago"
        elif diff.seconds > 60:
            return f"{diff.seconds // 60}m ago"
        else:
            return "just now"

    async def _handle_write_command(self, user_input: str) -> None:
        """Handle write command with enhanced interface."""
        await self._send_write_request()

    async def _handle_refresh(self) -> None:
        """Handle refresh command."""
        self.last_refresh = datetime.now()
        await self._update_stats()
        self._refresh_display()
        self._add_to_conversation("assistant", "Display refreshed and data updated")

    async def _handle_natural_language(self, user_input: str) -> None:
        """Handle natural language input by interpreting intent."""
        user_lower = user_input.lower()

        if any(word in user_lower for word in ["status", "how", "what's", "check"]):
            await self._handle_status()
        elif any(word in user_lower for word in ["events", "notes", "recent", "transcription"]):
            await self._handle_events()
        elif any(word in user_lower for word in ["write", "send", "message", "note"]):
            await self._send_write_request()
        elif any(word in user_lower for word in ["refresh", "update", "reload"]):
            await self._handle_refresh()
        else:
            suggestions = [
                "Try: 'status' to see system status",
                "Try: 'events' to see recent transcriptions",
                "Try: 'write' to send a message to Supernote",
                "Type 'help' for all commands"
            ]
            print(f"\n{Style.YELLOW}🤔 I'm not sure what you meant.{Style.RESET}")
            print(f"{Style.DIM}Here are some suggestions:{Style.RESET}")
            for suggestion in suggestions:
                print(f"  • {suggestion}")

            self._add_to_conversation("assistant", f"Didn't understand: '{user_input}' - showed suggestions")

    async def _update_stats(self) -> None:
        """Update statistics from recent events."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"http://localhost:3511/v1/subscribers/paia-supernote/events",
                    params={"limit": 50},
                    timeout=3
                )
                if response.status_code == 200:
                    data = response.json()
                    events = data.get("events", [])

                    # Count today's events
                    today = datetime.now().date()
                    transcriptions_today = 0
                    writes_today = 0

                    for event in events:
                        event_time = datetime.fromtimestamp(event.get("timestamp", 0))
                        if event_time.date() == today:
                            if "text" in event.get("payload", {}):
                                transcriptions_today += 1
                            elif "write" in event.get("event_type", "").lower():
                                writes_today += 1

                    self.stats["transcriptions_today"] = transcriptions_today
                    self.stats["write_requests_today"] = writes_today

        except Exception as e:
            log.debug("stats_update_error", error=str(e))

    async def _send_write_request(self) -> None:
        """Send a write request to Supernote with enhanced interface."""
        print(f"\n{Style.BOLD}✏️  Write to Supernote{Style.RESET}")
        print("─" * 30)

        # Get notebook name
        print(f"{Style.CYAN}📒 Notebook name:{Style.RESET}")
        notebook = input(f"  → ").strip()
        if not notebook:
            print(f"{Style.RED}❌ Notebook name required{Style.RESET}")
            return

        # Get content type
        print(f"\n{Style.CYAN}📝 Content type:{Style.RESET}")
        print(f"  {Style.DIM}1){Style.RESET} note     - Regular text note")
        print(f"  {Style.DIM}2){Style.RESET} summary  - Summary/report")
        print(f"  {Style.DIM}3){Style.RESET} task     - Task list")

        content_type_map = {"1": "note", "2": "summary", "3": "task"}
        choice = input(f"  → Choose (1-3): ").strip()
        content_type = content_type_map.get(choice, "note")

        print(f"\n{Style.CYAN}✍️  Content for '{content_type}':{Style.RESET}")
        print(f"  {Style.DIM}(Press Enter twice when finished){Style.RESET}")

        content_lines = []
        empty_count = 0

        while empty_count < 2:
            try:
                line = input("  ")
            except (EOFError, KeyboardInterrupt):
                print(f"\n{Style.YELLOW}Write cancelled{Style.RESET}")
                return

            if line.strip():
                content_lines.append(line)
                empty_count = 0
            else:
                empty_count += 1
                if empty_count == 1:
                    content_lines.append("")

        content = "\n".join(content_lines).strip()
        if not content:
            print(f"{Style.RED}❌ No content provided{Style.RESET}")
            return

        # Show preview
        print(f"\n{Style.BOLD}📋 Preview:{Style.RESET}")
        preview = content[:200] + ("..." if len(content) > 200 else "")
        print(f"  {Style.DIM}{preview}{Style.RESET}")
        print(f"  {Style.DIM}→ {content_type} for {notebook} ({len(content)} chars){Style.RESET}")

        confirm = input(f"\n{Style.YELLOW}Send this content? (y/N): {Style.RESET}").strip().lower()
        if confirm not in ['y', 'yes']:
            print(f"{Style.DIM}Write cancelled{Style.RESET}")
            return

        # Send write request via events
        try:
            await self.events._publish(
                event_type="supernote.write.requested",
                payload={
                    "agent": "braydon",
                    "notebook": notebook,
                    "content_type": content_type,
                    "content": content,
                    "timestamp": time.time()
                },
                dedupe_key=f"supernote.write.requested:user_board:{uuid.uuid4()}",
                occurred_at=datetime.now(timezone.utc),
            )
            print(f"{Style.GREEN}✅ Write request sent to {Style.BOLD}{notebook}{Style.RESET}{Style.GREEN}!{Style.RESET}")
            self._add_to_conversation("assistant", f"Sent {content_type} to notebook '{notebook}'", {
                "notebook": notebook,
                "content_type": content_type,
                "length": len(content)
            })

        except Exception as e:
            error_msg = f"Failed to send write request: {str(e)}"
            print(f"{Style.RED}❌ {error_msg}{Style.RESET}")
            self._add_to_conversation("system", error_msg)



async def main():
    """Main entry point for user board."""
    board = UserBoard()
    try:
        await board.start()
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    finally:
        await board.stop()


def cli():
    """Synchronous CLI entry point for setuptools scripts."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    except Exception as e:
        print(f"❌ Error starting user board: {e}")
        sys.exit(1)


if __name__ == "__main__":
    cli()
