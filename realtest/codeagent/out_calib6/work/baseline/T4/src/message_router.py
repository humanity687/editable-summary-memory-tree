import logging
import time
import uuid
from typing import Any, Dict, List, Optional

import aiohttp

from src.system_db import SystemDB

logger = logging.getLogger(__name__)


class MessageRouter:
    """Unified message bus — routes all messages between agents, users, and system.

    Agent outputs JSON actions. MessageRouter pulls outputs, then routes each
    action based on its type and target.
    """

    def __init__(self, db: SystemDB, process_manager=None, ws_server=None):
        self.db = db
        self.pm = process_manager
        self.ws = ws_server

    # ── Notification Handler ──

    async def handle_notification(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            agent_id = data.get("agent_id")
            if not agent_id:
                return {"code": 400, "message": "missing agent_id"}

            event = data.get("event", "")
            if event == "log":
                event_data = data.get("data", {})
                self.db.add_log(
                    event_data.get("level", "INFO"),
                    f"agent:{agent_id}",
                    event_data.get("message", ""),
                )
                return {"code": 200, "message": "log recorded"}

            if event != "has_outputs":
                return {"code": 200, "message": "no action"}

            outputs = await self._fetch_agent_outputs(agent_id)
            if not outputs:
                return {"code": 200, "message": "no outputs"}

            for output in outputs:
                await self._route_action(output)

            return {"code": 200, "message": "routed", "count": len(outputs)}

        except Exception:
            logger.exception("handle_notification failed")
            return {"code": 500, "message": "internal error"}

    # ── Fetch Agent Outputs ──

    async def _fetch_agent_outputs(self, agent_id: str) -> List[Dict[str, Any]]:
        try:
            agent = self._find_agent(agent_id)
            if not agent:
                logger.warning("Agent %s not found for output fetch", agent_id)
                return []

            port = agent.get("port", 0)
            if not port or port <= 0:
                return []

            url = f"http://127.0.0.1:{port}/api/outputs"
            timeout = aiohttp.ClientTimeout(total=2)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return []
                    result = await resp.json()
                    return result.get("data", [])
        except Exception:
            logger.exception("_fetch_agent_outputs failed for %s", agent_id)
            return []

    # ── Forward to Agent ──

    async def forward_to_agent(self, target_id: str, message: Dict[str, Any]) -> bool:
        try:
            agent = self._find_agent(target_id)
            if not agent:
                logger.warning("forward_to_agent: target %s not found", target_id)
                return False

            port = agent.get("port", 0)
            if not port or port <= 0:
                return False

            url = f"http://127.0.0.1:{port}/api/input"
            timeout = aiohttp.ClientTimeout(total=2)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=message) as resp:
                    return resp.status in (200, 202)
        except Exception:
            logger.exception("forward_to_agent failed for %s", target_id)
            return False

    # ── Broadcast to All Agents ──

    async def broadcast_to_agents(self, message: Dict[str, Any]) -> List[str]:
        succeeded = []
        sender = (message.get("from_agent") or "").replace("agent:", "")
        try:
            agents = self.pm.get_online_agents() if self.pm else []
            for agent in agents:
                aid = agent.get("agent_id", "")
                if not aid:
                    continue
                if aid == sender:
                    continue  # 不发回给自己
                if await self.forward_to_agent(aid, message):
                    succeeded.append(aid)
        except Exception:
            logger.exception("broadcast_to_agents failed")
        return succeeded

    # ── Core Routing Logic ──

    async def _route_action(self, action: Dict[str, Any]) -> None:
        """Route a single action based on type and target."""
        try:
            action_type = action.get("type", "message")
            action_to = action.get("to", "")
            from_agent = action.get("from_agent", "unknown")
            # 确保 from_agent 带正确前缀（bare name → agent:xxx）
            if from_agent and not from_agent.startswith("agent:") and not from_agent.startswith("user:"):
                from_agent = f"agent:{from_agent}"
            content = action.get("content", "")
            msg_id = str(uuid.uuid4())

            # 解析 source/target 类型
            def _parse_peer_type(peer: str) -> str:
                for pfx in ("agent:", "user:", "broadcast:"):
                    if peer.startswith(pfx):
                        return pfx.rstrip(":")
                return "unknown"

            source_type = _parse_peer_type(from_agent)
            target_type = _parse_peer_type(action_to) if action_to else "broadcast"
            source_name = from_agent.split(":", 1)[1] if ":" in from_agent else from_agent
            target_name = action_to.split(":", 1)[1] if ":" in action_to else action_to
            ts = action.get("timestamp", time.time())

            message = {
                # ── 顶层（向后兼容） ──
                "type": action_type,
                "from_agent": from_agent,
                "from": from_agent,
                "to": action_to,
                "content": content,
                "source": from_agent,
                "timestamp": ts,
                "msg_id": msg_id,
                # ── 结构化数据 ──
                "data": {
                    "content": content,
                },
                # ── 元数据 ──
                "metadata": {
                    "msg_id": msg_id,
                    "timestamp": ts,
                    "source": from_agent,
                    "source_type": source_type,
                    "source_name": source_name,
                    "target": action_to,
                    "target_type": target_type,
                    "target_name": target_name,
                },
                # ── 完整 payload（含原始 action） ──
                "payload": {"content": content, **action},
            }

            if action_type in ("message", "shell_result", "status"):
                await self._route_message(action_to, from_agent, message, content)
            elif action_type == "command":
                await self._route_command(action, from_agent)
            # action_type == "code" → 丢弃，代码由 Agent 自己的 Shell 执行

            # Persist to global message archive
            self.db.save_global_message(
                from_agent=from_agent,
                to_target=action_to or "broadcast",
                content=content,
                msg_type=action_type,
            )

        except Exception:
            logger.exception("_route_action failed")

    async def _route_message(self, action_to: str, from_agent: str,
                             message: dict, content: str):
        if action_to.startswith("user:"):
            username = action_to[len("user:"):]
            if self.ws:
                self.ws.push_to_user(username, message)

        elif action_to.startswith("agent:"):
            await self.forward_to_agent(action_to, message)

        elif action_to == "broadcast:agents":
            await self.broadcast_to_agents(message)

        elif action_to == "broadcast:users":
            if self.ws:
                self.ws.broadcast_to_users(message)

        else:
            # Default (empty or unknown target): broadcast to users + agent channel
            # Patch the payload so frontend filter passes it through
            message["to"] = "broadcast:users"
            message["payload"]["to"] = "broadcast:users"
            if self.ws:
                self.ws.push_to_channel(f"chat:{from_agent}", message)

    async def _route_command(self, action: dict, from_agent: str):
        """Handle system-level commands from agents."""
        cmd_name = action.get("command", action.get("name", ""))
        cmd_params = action.get("params", {})

        if cmd_name == "emergency-stop":
            if self.pm:
                self.pm.emergency_stop_all()
            if self.ws:
                self.ws.broadcast_system_notification(
                    "warning", f"Emergency stop triggered by {from_agent}"
                )

        elif cmd_name == "set_wakeup_interval":
            seconds = cmd_params.get("seconds", 10)
            agent_id = from_agent[len("agent:"):] if from_agent.startswith("agent:") else from_agent
            state = self.db.get_agent_state(agent_id)
            if state and state.get("port", 0) > 0:
                port = state["port"]
                try:
                    import urllib.request, json
                    data = json.dumps({"wakeup_interval": seconds}).encode()
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/config",
                        data=data, method="PUT",
                        headers={"Content-Type": "application/json"}
                    )
                    urllib.request.urlopen(req, timeout=3)
                except Exception:
                    pass

    # ── Helpers ──

    def _find_agent(self, identifier: str) -> Optional[Dict[str, Any]]:
        # Strip "agent:" prefix if present
        agent_id = identifier[len("agent:"):] if identifier.startswith("agent:") else identifier
        # Prefer DB state (has port), fall back to config
        state = self.db.get_agent_state(agent_id)
        if state and state.get("port", 0) > 0:
            return state
        if self.pm:
            return self.pm.get_agent(agent_id)
        return state
