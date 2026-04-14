"""
ABOUTME: paia-supernote main service entry point
Author: Braydon McCormick <braydon@braydondm.com>
Purpose: Main daemon that orchestrates file watching, content processing, and agent integration
"""

import asyncio
import signal
import sys
from typing import Dict, Any
from pathlib import Path

from .events import EventsManager
from .watcher import SupernoteWatcher
from .reader import SupernoteReader
from .writer import SupernoteWriter
from .uploader import SupernoteUploader


class SupernoteService:
    """Main paia-supernote service that coordinates all components."""

    def __init__(self):
        """Initialize the Supernote service."""
        self.events = EventsManager()
        self.watcher = SupernoteWatcher(on_note_changed=self._on_note_changed)
        self.reader = SupernoteReader()
        self.writer = SupernoteWriter()
        self.uploader = SupernoteUploader()
        self.running = False

    async def start(self) -> None:
        """Start the Supernote service."""
        print("Starting paia-supernote service...")

        try:
            # Start events connection
            await self.events.start()
            self.events.register_write_handler(self._handle_write_request)

            # Start uploader browser session
            await self.uploader.start()

            # Start file watcher
            self.watcher.start()

            self.running = True
            print("paia-supernote service started successfully")

            # Keep service running
            while self.running:
                await asyncio.sleep(1)

        except Exception as e:
            print(f"Failed to start service: {e}")
            await self.stop()

    async def stop(self) -> None:
        """Stop the Supernote service."""
        print("Stopping paia-supernote service...")
        self.running = False

        # Stop components
        try:
            self.watcher.stop()
            await self.uploader.stop()
            await self.events.stop()
        except Exception as e:
            print(f"Error during shutdown: {e}")

        print("paia-supernote service stopped")

    def _on_note_changed(self, file_path: Path, notebook_name: str) -> None:
        """
        Handle file change events from watcher.

        Args:
            file_path: Path to changed .note file
            notebook_name: Extracted notebook name (stem of the file)
        """
        print(f"Processing file change: {file_path} ({notebook_name})")

        # Process file asynchronously
        asyncio.create_task(self._process_file_change(str(file_path), notebook_name))

    async def _process_file_change(self, file_path: str, notebook_name: str) -> None:
        """
        Process a changed .note file.

        Args:
            file_path: Path to changed .note file
            notebook_name: Extracted notebook name
        """
        try:
            # Extract and transcribe content
            content_items = await self.reader.process_file(file_path)

            for item in content_items:
                notebook = notebook_name
                page = item["page_number"]
                text = item["transcription"]
                content_type = item["content_type"]

                # Publish transcription to folio
                await self.events.publish_note_transcribed(notebook, page, text)

                # Handle content type specific processing
                if content_type == "task":
                    await self._handle_task_content(item, notebook)
                elif content_type == "strategy_snippet":
                    await self._handle_strategy_snippet(item, notebook)

                # Handle checkbox completions
                for checkbox in item["checkbox_changes"]:
                    await self.events.publish_checkbox_completed(
                        checkbox["task_text"], notebook, page
                    )

        except Exception as e:
            print(f"Error processing file change {file_path}: {e}")

    async def _handle_task_content(self, item: Dict[str, Any], notebook: str) -> None:
        """
        Handle task content (□/○ markers).

        Args:
            item: Content item with task information
            notebook: Notebook name
        """
        # TODO: Extract new □/○ tasks and add to paia-work
        print(f"Task content detected in {notebook}: {item['transcription'][:100]}...")

    async def _handle_strategy_snippet(self, item: Dict[str, Any], notebook: str) -> None:
        """
        Handle strategy snippet detection.

        Args:
            item: Content item with snippet information
            notebook: Notebook name
        """
        # Route to appropriate agent based on notebook
        agent = "Caroline" if notebook == "LFW" else "Ingrid"

        await self.events.publish_snippet_detected(
            notebook=notebook,
            page=item["page_number"],
            text=item["transcription"],
            agent=agent
        )

    async def _handle_write_request(self, event_data: Dict[str, Any]) -> None:
        """
        Handle write request events from agents.

        Args:
            event_data: Write request event data
        """
        try:
            agent = event_data["agent"]
            notebook = event_data["notebook"]
            content_type = event_data["content_type"]
            content = event_data["content"]

            print(f"Processing write request from {agent} to {notebook}")

            # Render content to .note page (RATTA_RLE encoded bitmap)
            page_data = self.writer.render_page(agent, content, content_type)

            # Determine notebook path
            # TODO: merge page_data into existing notebook via supernotelib.merge()
            notebook_path = str(
                Path(f"~/Supernote/{notebook}.note").expanduser()
            )

            # Upload to cloud
            success = await self.uploader.upload_notebook(
                notebook_path, f"{notebook}.note"
            )

            if success:
                print(f"Successfully wrote {agent}'s content to {notebook}")
            else:
                print(f"Failed to upload {agent}'s content to {notebook}")

        except Exception as e:
            print(f"Error handling write request: {e}")


def main() -> None:
    """Main entry point for paia-supernote service."""
    service = SupernoteService()

    # Setup signal handlers for graceful shutdown
    def signal_handler(signum, frame):
        print(f"Received signal {signum}, shutting down...")
        asyncio.create_task(service.stop())

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # Run the service
        asyncio.run(service.start())
    except KeyboardInterrupt:
        print("Service interrupted")
    except Exception as e:
        print(f"Service error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()