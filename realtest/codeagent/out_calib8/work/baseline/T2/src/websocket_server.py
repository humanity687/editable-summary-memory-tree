import json
import time
import uuid
import logging
import threading
import asyncio
from typing import Dict, Any, Optional, Callable, List

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class WebSocketServer:
    def __init__(self):
        self._connections: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ── 消息构造 ──

    @staticmethod
    def _make_message(channel: str, msg_type: str, payload: Any,
                      msg_id: Optional[str] = None) -> dict:
        return {
            "channel": channel,
            "type": msg_type,
            "payload": payload,
            "timestamp": time.time(),
            "msg_id": msg_id or uuid.uuid4().hex,
        }

    # ── Channel 订阅管理 ──

    def _subscribe(self, conn_id: str, channel: str):
        with self._lock:
            if conn_id in self._connections:
                conn = self._connections[conn_id]
                if channel not in conn["channels"]:
                    conn["channels"].append(channel)

    def _unsubscribe(self, conn_id: str, channel: str):
        with self._lock:
            if conn_id in self._connections:
                conn = self._connections[conn_id]
                if channel in conn["channels"]:
                    conn["channels"].remove(channel)

    # ── 推送方法 ──

    @staticmethod
    def _enrich_payload(message: dict) -> dict:
        """将消息顶层元数据合并到 payload 中，确保前端能读取 from_agent/source/to"""
        payload = message.get("payload", message)
        if isinstance(payload, dict):
            for key in ("from_agent", "source", "to"):
                if key in message and key not in payload:
                    payload[key] = message[key]
        return payload

    def push_to_channel(self, channel: str, message: dict):
        formatted = self._make_message(
            channel,
            message.get("type", "message"),
            self._enrich_payload(message),
            message.get("msg_id"),
        )
        payload = json.dumps(formatted, ensure_ascii=False)
        targets = []
        with self._lock:
            for conn in self._connections.values():
                if channel in conn.get("channels", []):
                    targets.append(conn["websocket"])
        for ws in targets:
            self._safe_send(ws, payload)

    def push_to_user(self, user_id: str, message: dict):
        channel = message.get("channel", f"chat:{message.get('from_agent', '')}")
        formatted = self._make_message(
            channel,
            message.get("type", "message"),
            self._enrich_payload(message),
            message.get("msg_id"),
        )
        payload = json.dumps(formatted, ensure_ascii=False)
        targets = []
        with self._lock:
            for conn in self._connections.values():
                if conn.get("user_id") == user_id:
                    targets.append(conn["websocket"])
        for ws in targets:
            self._safe_send(ws, payload)

    def broadcast_to_users(self, message: dict):
        with self._lock:
            user_ids = list(set(
                c.get("user_id") for c in self._connections.values() if c.get("user_id")
            ))
        for uid in user_ids:
            self.push_to_user(uid, message)

    def get_connected_users(self) -> List[str]:
        with self._lock:
            return list(set(
                c.get("user_id", "") for c in self._connections.values()
                if c.get("user_id")
            ))

    # ── Agent 上下线广播 ──

    def broadcast_agent_online(self, agent_id: str):
        self.push_to_channel("system", {
            "type": "agent_online",
            "payload": {"agent_id": agent_id},
        })

    def broadcast_agent_offline(self, agent_id: str):
        self.push_to_channel("system", {
            "type": "agent_offline",
            "payload": {"agent_id": agent_id},
        })

    def broadcast_system_notification(self, level: str, content: str):
        self.push_to_channel("system", {
            "type": "system_notification",
            "payload": {"level": level, "content": content},
        })

    # ── WebSocket 连接处理 ──

    async def _handle_ws(self, websocket: WebSocket):
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        await websocket.accept()
        conn_id = uuid.uuid4().hex
        with self._lock:
            self._connections[conn_id] = {
                "websocket": websocket,
                "user_id": None,
                "channels": [],
            }

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                msg_type = data.get("type", "")
                payload = data.get("payload", {})

                if msg_type == "auth":
                    user_id = payload.get("user_id", "")
                    if user_id:
                        with self._lock:
                            self._connections[conn_id]["user_id"] = user_id
                        self._subscribe(conn_id, "system")
                        await self._safe_send_ws(websocket, self._make_message(
                            "system", "auth_ok", {"user_id": user_id}
                        ))

                elif msg_type == "subscribe":
                    channel = payload.get("channel", "")
                    if channel:
                        self._subscribe(conn_id, channel)
                        await self._safe_send_ws(websocket, self._make_message(
                            "system", "subscribed", {"channel": channel}
                        ))

                elif msg_type == "unsubscribe":
                    channel = payload.get("channel", "")
                    if channel:
                        self._unsubscribe(conn_id, channel)
                        await self._safe_send_ws(websocket, self._make_message(
                            "system", "unsubscribed", {"channel": channel}
                        ))

                elif msg_type == "chat_message":
                    # 前端通过 WS 发来的聊天消息，返回给调用方处理
                    target = data.get("to", "")
                    content = payload.get("content", "")
                    source = payload.get("source") or self._connections[conn_id].get("user_id", "anonymous")
                    await self._handle_incoming_chat(websocket, conn_id, source, target, content)

        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            with self._lock:
                self._connections.pop(conn_id, None)

    async def _handle_incoming_chat(self, websocket: WebSocket, conn_id: str,
                                    source: str, target: str, content: str):
        """由外部注入的 chat 处理器接管"""
        handler = getattr(self, '_on_incoming_chat', None)
        if handler:
            await handler(source, target, content)

    @staticmethod
    async def _safe_send_ws(websocket: WebSocket, msg: dict):
        try:
            await websocket.send_text(json.dumps(msg, ensure_ascii=False))
        except Exception:
            pass

    def _safe_send(self, websocket: WebSocket, payload: str):
        try:
            loop = self._loop or asyncio.get_running_loop()
            asyncio.run_coroutine_threadsafe(
                websocket.send_text(payload), loop
            )
        except RuntimeError:
            try:
                loop = asyncio.get_event_loop()
                asyncio.run_coroutine_threadsafe(
                    websocket.send_text(payload), loop
                )
            except Exception as e2:
                logger.warning("_safe_send fallback failed: %s", e2)
        except Exception as e:
            logger.warning("_safe_send failed: %s", e)

    def get_ws_handler(self) -> Callable:
        async def ws_endpoint(websocket: WebSocket):
            await self._handle_ws(websocket)
        return ws_endpoint
