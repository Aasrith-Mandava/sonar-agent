"""FastAPI WebSocket endpoints for live pipeline progress and agent logs."""

import asyncio
import json
import logging
from collections import defaultdict
from typing import Dict, List

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ws", tags=["websocket"])


class ConnectionManager:
    def __init__(self):
        # scan_run_id -> list of active websockets
        self.pipeline_connections: Dict[str, List[WebSocket]] = defaultdict(list)
        # global log listeners
        self.log_connections: List[WebSocket] = []

    async def connect_pipeline(self, websocket: WebSocket, scan_run_id: str):
        await websocket.accept()
        self.pipeline_connections[scan_run_id].append(websocket)

    def disconnect_pipeline(self, websocket: WebSocket, scan_run_id: str):
        if scan_run_id in self.pipeline_connections:
            try:
                self.pipeline_connections[scan_run_id].remove(websocket)
            except ValueError:
                pass

    async def broadcast_pipeline(self, scan_run_id: str, message: dict):
        if scan_run_id not in self.pipeline_connections:
            return
        
        failed_connections = []
        for connection in self.pipeline_connections[scan_run_id]:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"WebSocket broadcast failed: {e}")
                failed_connections.append(connection)
                
        for failed in failed_connections:
            self.disconnect_pipeline(failed, scan_run_id)

    async def connect_logs(self, websocket: WebSocket):
        await websocket.accept()
        self.log_connections.append(websocket)

    def disconnect_logs(self, websocket: WebSocket):
        try:
            self.log_connections.remove(websocket)
        except ValueError:
            pass

    async def broadcast_log(self, log_entry: dict):
        failed_connections = []
        for connection in self.log_connections:
            try:
                await connection.send_json(log_entry)
            except Exception as e:
                logger.error(f"WebSocket log broadcast failed: {e}")
                failed_connections.append(connection)
                
        for failed in failed_connections:
            self.disconnect_logs(failed)

manager = ConnectionManager()


@router.websocket("/pipeline/{scan_run_id}")
async def websocket_pipeline(websocket: WebSocket, scan_run_id: str):
    await manager.connect_pipeline(websocket, scan_run_id)
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect_pipeline(websocket, scan_run_id)


@router.websocket("/logs")
async def websocket_logs(websocket: WebSocket):
    await manager.connect_logs(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect_logs(websocket)
