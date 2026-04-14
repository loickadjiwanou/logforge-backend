from fastapi import WebSocket
from typing import List, Dict, Set
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


class WebSocketManager:
    def __init__(self):
        # map project_id -> set of WebSocket connections
        self.subscriptions: Dict[str, Set[WebSocket]] = defaultdict(set)
        # track all active connections to clean up easily
        self.all_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.all_connections.add(websocket)
        logger.info(f"WebSocket connected. Total: {len(self.all_connections)}")

    def disconnect(self, websocket: WebSocket):
        # Remove from all project subscriptions
        for project_id in list(self.subscriptions.keys()):
            if websocket in self.subscriptions[project_id]:
                self.subscriptions[project_id].remove(websocket)
                if not self.subscriptions[project_id]:
                    del self.subscriptions[project_id]
        
        if websocket in self.all_connections:
            self.all_connections.remove(websocket)
        logger.info(f"WebSocket disconnected. Total: {len(self.all_connections)}")

    def subscribe(self, websocket: WebSocket, project_id: str):
        if websocket in self.all_connections:
            self.subscriptions[project_id].add(websocket)
            logger.info(f"Client subscribed to project: {project_id}")

    def unsubscribe(self, websocket: WebSocket, project_id: str):
        if project_id in self.subscriptions and websocket in self.subscriptions[project_id]:
            self.subscriptions[project_id].remove(websocket)
            if not self.subscriptions[project_id]:
                del self.subscriptions[project_id]
            logger.info(f"Client unsubscribed from project: {project_id}")

    async def broadcast(self, project_id: str, data: dict):
        """Send message only to subscribers of a specific project and 'all' subscribers."""
        # Subscribers specifically for this project
        targets = set(self.subscriptions.get(project_id, []))
        # Subscribers for everything
        targets.update(self.subscriptions.get('all', []))

        if not targets:
            return

        disconnected = []
        for conn in targets:
            try:
                await conn.send_json(data)
            except Exception as e:
                logger.warning(f"WebSocket send failed, disconnecting client: {e}")
                disconnected.append(conn)
        
        for conn in disconnected:
            self.disconnect(conn)

    async def broadcast_all(self, data: dict):
        """Broadcast to every single active connection."""
        disconnected = []
        for conn in self.all_connections:
            try:
                await conn.send_json(data)
            except Exception as e:
                logger.warning(f"WebSocket send failed, disconnecting client: {e}")
                disconnected.append(conn)
        
        for conn in disconnected:
            self.disconnect(conn)


ws_manager = WebSocketManager()
