import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any


class InstructionBroadcaster:
    """Fan-out hub for instruction change stream events to SSE subscribers."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()

    async def publish(self, instruction: dict[str, Any]) -> None:
        async with self._lock:
            subscribers = list(self._subscribers)
        for queue in subscribers:
            await queue.put(instruction)

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        async with self._lock:
            self._subscribers.add(queue)
        try:
            while True:
                instruction = await queue.get()
                yield instruction
        finally:
            async with self._lock:
                self._subscribers.discard(queue)

    @staticmethod
    def sse_payload(instruction: dict[str, Any]) -> str:
        return f"data: {json.dumps(instruction, separators=(',', ':'))}\n\n"
