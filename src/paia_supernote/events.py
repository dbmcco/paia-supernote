"""
ABOUTME: PAIA events integration module
Author: Braydon McCormick <braydon@braydondm.com>
Purpose: Handles event publishing/subscribing with paia-events system at port 3511
"""

import json
import asyncio
from typing import Dict, Any, Callable, Optional
import asyncio_mqtt
from pydantic import BaseModel


class SupernoteEvent(BaseModel):
    """Base class for Supernote events."""
    event_type: str
    timestamp: float
    data: Dict[str, Any]


class WriteRequestedEvent(SupernoteEvent):
    """Event for requesting agent write to Supernote."""
    agent: str
    notebook: str
    content_type: str
    content: str


class NoteTranscribedEvent(SupernoteEvent):
    """Event for transcribed note content."""
    notebook: str
    page: int
    text: str


class CheckboxCompletedEvent(SupernoteEvent):
    """Event for completed checkbox/task."""
    task_id: str
    notebook: str
    page: int


class SnippetDetectedEvent(SupernoteEvent):
    """Event for detected strategy snippet."""
    notebook: str
    page: int
    text: str
    agent: str


class EventsManager:
    """Manages event publishing and subscription for paia-supernote."""

    def __init__(self, broker_host: str = "localhost", broker_port: int = 3511):
        """
        Initialize events manager.

        Args:
            broker_host: MQTT broker host
            broker_port: MQTT broker port (paia-events default: 3511)
        """
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.client: Optional[asyncio_mqtt.Client] = None
        self.event_handlers: Dict[str, Callable] = {}

    async def start(self) -> None:
        """Start event client and subscribe to relevant topics."""
        try:
            self.client = asyncio_mqtt.Client(
                hostname=self.broker_host,
                port=self.broker_port
            )
            await self.client.__aenter__()

            # Subscribe to inbound events (agents → service)
            await self.client.subscribe("supernote/write_requested")

            # Start message handler
            asyncio.create_task(self._handle_messages())
            print(f"Connected to paia-events at {self.broker_host}:{self.broker_port}")

        except Exception as e:
            print(f"Failed to connect to event broker: {e}")

    async def stop(self) -> None:
        """Stop event client."""
        if self.client:
            try:
                await self.client.__aexit__(None, None, None)
            except Exception as e:
                print(f"Error stopping event client: {e}")

    async def publish_note_transcribed(
        self, notebook: str, page: int, text: str
    ) -> None:
        """
        Publish note transcribed event → folio.

        Args:
            notebook: Notebook name
            page: Page number
            text: Transcribed text
        """
        event = NoteTranscribedEvent(
            event_type="note_transcribed",
            timestamp=asyncio.get_event_loop().time(),
            data={},
            notebook=notebook,
            page=page,
            text=text
        )
        await self._publish("supernote/note_transcribed", event.model_dump())

    async def publish_checkbox_completed(
        self, task_id: str, notebook: str, page: int
    ) -> None:
        """
        Publish checkbox completed event → paia-work.

        Args:
            task_id: Task identifier
            notebook: Notebook name
            page: Page number
        """
        event = CheckboxCompletedEvent(
            event_type="checkbox_completed",
            timestamp=asyncio.get_event_loop().time(),
            data={},
            task_id=task_id,
            notebook=notebook,
            page=page
        )
        await self._publish("supernote/checkbox_completed", event.model_dump())

    async def publish_snippet_detected(
        self, notebook: str, page: int, text: str, agent: str
    ) -> None:
        """
        Publish snippet detected event → Caroline or Ingrid.

        Args:
            notebook: Notebook name
            page: Page number
            text: Snippet text
            agent: Target agent (Caroline, Ingrid)
        """
        event = SnippetDetectedEvent(
            event_type="snippet_detected",
            timestamp=asyncio.get_event_loop().time(),
            data={},
            notebook=notebook,
            page=page,
            text=text,
            agent=agent
        )
        await self._publish("supernote/snippet_detected", event.model_dump())

    def register_write_handler(self, handler: Callable[[Dict[str, Any]], None]) -> None:
        """
        Register handler for write_requested events.

        Args:
            handler: Function to handle write requests
        """
        self.event_handlers["write_requested"] = handler

    async def _publish(self, topic: str, data: Dict[str, Any]) -> None:
        """
        Publish event to MQTT broker.

        Args:
            topic: MQTT topic
            data: Event data
        """
        if not self.client:
            print("Event client not connected")
            return

        try:
            message = json.dumps(data)
            await self.client.publish(topic, message)
        except Exception as e:
            print(f"Failed to publish to {topic}: {e}")

    async def _handle_messages(self) -> None:
        """Handle incoming MQTT messages."""
        if not self.client:
            return

        try:
            async with self.client.messages() as messages:
                async for message in messages:
                    try:
                        data = json.loads(message.payload.decode())
                        topic_parts = message.topic.value.split("/")

                        if len(topic_parts) >= 2:
                            event_type = topic_parts[1]  # e.g., "write_requested"
                            handler = self.event_handlers.get(event_type)
                            if handler:
                                handler(data)

                    except Exception as e:
                        print(f"Error processing message: {e}")

        except Exception as e:
            print(f"Error in message handler: {e}")