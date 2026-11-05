import asyncio
import json
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List
from app.services.clock import ClockService

class FeedBroker:
    def __init__(self):
        self._subscribers: List[asyncio.Queue] = []
        self._history: List[Dict[str, Any]] = []

    def publish(self, event_type: str, data: Dict[str, Any]):
        item = {
            "type": event_type,
            "data": data,
            "timestamp": ClockService.now().isoformat(),
        }
        self._history.append(item)
        if len(self._history) > 200:
            self._history.pop(0)

        for q in self._subscribers:
            try:
                q.put_nowait(item)
            except Exception:
                pass

    async def subscribe(self) -> AsyncGenerator[str, None]:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(queue)
        try:
            # Yield initial connect event
            yield f"event: connect\ndata: {json.dumps({'status': 'connected', 'history_count': len(self._history)})}\n\n"
            while True:
                item = await queue.get()
                payload = json.dumps(item)
                yield f"event: {item['type']}\ndata: {payload}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if queue in self._subscribers:
                self._subscribers.remove(queue)

    def get_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        return list(reversed(self._history[-limit:]))


feed_broker = FeedBroker()
