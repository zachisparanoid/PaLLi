import logging
import time
from typing import List, Dict, Any, Optional
from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

class SocketManager:
    """
    Manages WebSocket connections and broadcasts real-time events 
    to connected clients (dashboards).
    """
    def __init__(self):
        # List of active WebSocket connections, stored as dicts {"ws": WebSocket, "user_id": int | None}
        self.active_connections: List[Dict[str, Any]] = []
        
    async def connect(self, websocket: WebSocket, user_id: Optional[int] = None):
        """Accept a new WebSocket connection."""
        await websocket.accept()
        self.active_connections.append({"ws": websocket, "user_id": user_id})
        logger.info(f"🔌 New WebSocket client connected (User: {user_id}). Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        """Remove a WebSocket connection."""
        self.active_connections = [c for c in self.active_connections if c["ws"] != websocket]
        logger.info(f"🔌 WebSocket client disconnected. Remaining: {len(self.active_connections)}")

    async def emit(self, event_type: str, data: Dict[str, Any], user_id: Optional[int] = None):
        """
        Broadcast a structured event to connected clients.
        
        Args:
            event_type: Category of event (e.g., 'processing_start', 'intent_detected')
            data: The payload data to send
            user_id: Optional user_id to filter who receives this.
                     If provided, only that user receives it.
                     If None, all connected clients receive it (global broadcast).
        """
        message = {
            "type": event_type,
            "data": data,
            "timestamp": time.time(),
        }
        
        targets: List[Dict[str, Any]] = []
        for connection in self.active_connections:
            # Filter logic:
            # If target user_id is set, connection must match it.
            if user_id is not None and connection["user_id"] != user_id:
                continue

            targets.append(connection)

        async def _send(connection: Dict[str, Any]):
            try:
                # Avoid head-of-line blocking on a single slow client.
                await connection["ws"].send_json(message)
                return None
            except WebSocketDisconnect as e:
                return e
            except Exception as e:
                return e

        dead_connections = []
        if targets:
            import asyncio

            tasks = [asyncio.create_task(_send(conn)) for conn in targets]
            results = await asyncio.gather(*tasks, return_exceptions=False)
            for conn, err in zip(targets, results):
                if err is None:
                    continue
                if not isinstance(err, WebSocketDisconnect):
                    logger.warning(f"⚠️ Failed to send to WebSocket client: {err}")
                dead_connections.append(conn)
        
        # Cleanup
        for dead in dead_connections:
            if dead in self.active_connections:
                self.active_connections.remove(dead)

# Global instance
socket_manager = SocketManager()
