import asyncio
import os
import sys
import json
import logging
import time
import subprocess
import signal
import threading
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from typing import Dict, Any, Optional, List

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.system_db import SystemDB
from src.process_manager import ProcessManager
from src.websocket_server import WebSocketServer
from src.message_router import MessageRouter

# 日志：同时输出到控制台和 main.log
log_dir = os.path.dirname(os.path.abspath(__file__))
log_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
_file_handler = RotatingFileHandler(
    os.path.join(log_dir, "main.log"), maxBytes=20*1024*1024, backupCount=5, encoding="utf-8")
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(log_fmt)
_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(log_fmt)
logging.basicConfig(level=logging.DEBUG, handlers=[_file_handler, _console_handler])
logger = logging.getLogger("main")


# ── Load Config ──

def load_config() -> dict:
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if os.path.isfile(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

config = load_config()

# ── Cloudflare Tunnel ──

_tunnel_process = None
_tunnel_connected = False
_tunnel_url = ""       # 快速隧道生成的 trycloudflare.com 地址
_tunnel_log = []       # tunnel 完整日志（最多保留 500 行）
_MAX_TUNNEL_LOG = 500

def _start_tunnel():
    global _tunnel_process, _tunnel_connected, _tunnel_url

    # 默认不启用 tunnel，需在 config.json 中显式开启
    if not config.get("tunnel_enabled", False):
        logger.info("Cloudflare Tunnel disabled (tunnel_enabled not set)")
        return

    tunnel_name = config.get("cloudflare_tunnel_name", "")
    token = config.get("cloudflare_tunnel_token", "")
    port = config.get("main_process_port", 8000)

    # 优先级：命名隧道名 > token > 快速隧道
    if tunnel_name:
        cmd = ["cloudflared", "tunnel", "run", tunnel_name]
        logger.info("Starting Cloudflare tunnel '%s'...", tunnel_name)
    elif token:
        cmd = ["cloudflared", "tunnel", "run", "--token", token]
        logger.info("Starting Cloudflare named tunnel (token)...")
    else:
        cmd = ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"]
        logger.info("Starting Cloudflare quick tunnel (no config)...")

    try:
        _tunnel_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        _tunnel_connected = True

        def _read_tunnel_output():
            global _tunnel_connected, _tunnel_url
            for line in _tunnel_process.stdout:
                line_s = line.strip()
                if line_s:
                    _tunnel_log.append(line_s)
                    if len(_tunnel_log) > _MAX_TUNNEL_LOG:
                        _tunnel_log.pop(0)

                    # 捕捉快速隧道生成的 URL:  https://xxx.trycloudflare.com
                    if not _tunnel_url and "trycloudflare.com" in line_s:
                        import re
                        m = re.search(r'https://[-\w]+\.trycloudflare\.com', line_s)
                        if m:
                            _tunnel_url = m.group(0)
                            logger.info("Cloudflare Tunnel URL: %s", _tunnel_url)

                    if "inf" in line_s.lower():
                        logger.info("Cloudflare Tunnel: %s", line_s[:200])
            _tunnel_process.wait()
            _tunnel_connected = False
            logger.warning("Cloudflare Tunnel process exited (code=%s)", _tunnel_process.returncode)

        threading.Thread(target=_read_tunnel_output, daemon=True).start()
        logger.info("Cloudflare Tunnel started (PID: %d)", _tunnel_process.pid)
    except FileNotFoundError:
        logger.warning("cloudflared binary not found, tunnel disabled")
    except Exception as e:
        logger.error("Failed to start Cloudflare Tunnel: %s", e)

def _stop_tunnel():
    global _tunnel_process, _tunnel_connected
    if _tunnel_process is None:
        return
    logger.info("Stopping Cloudflare Tunnel...")
    try:
        _tunnel_process.send_signal(signal.SIGTERM)
        try:
            _tunnel_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _tunnel_process.kill()
    except Exception as e:
        logger.warning("Error stopping tunnel: %s", e)
    _tunnel_process = None
    _tunnel_connected = False
    logger.info("Cloudflare Tunnel stopped")

# ── Initialize Components ──

db = SystemDB(config.get("system_db_path"))
ws_server = WebSocketServer()
pm = ProcessManager(db=db, ws_server=ws_server)
router = MessageRouter(db=db, process_manager=pm, ws_server=ws_server)

# 日志写入 DB 时同步推送到 WebSocket system channel
def _on_db_log(level: str, source: str, message: str):
    ws_server.push_to_channel("system", {
        "type": "log",
        "payload": {"level": level, "source": source, "content": message},
    })
db.on_log = _on_db_log

# Wire incoming chat from WebSocket → MessageRouter
async def _on_incoming_chat(source: str, target: str, content: str):
    import uuid as _uuid
    ts = time.time()
    mid = _uuid.uuid4().hex
    if target.startswith("agent:"):
        target_type = "agent"
        target_name = target[6:]
    elif target.startswith("user:"):
        target_type = "user"
        target_name = target[5:]
    else:
        target_type = "unknown"
        target_name = target

    base_msg = {
        "type": "text",
        "from": f"user:{source}",
        "from_agent": f"user:{source}",
        "to": target,
        "content": content,
        "source": source,
        "timestamp": ts,
        "msg_id": mid,
        "data": {"content": content},
        "metadata": {
            "msg_id": mid,
            "timestamp": ts,
            "source": f"user:{source}",
            "source_type": "user",
            "source_name": source,
            "target": target,
            "target_type": target_type,
            "target_name": target_name,
        },
        "payload": {"content": content, "source": source},
    }

    if target.startswith("agent:"):
        base_msg["conversation_id"] = "default"
        db.save_global_message(from_agent=f"user:{source}", to_target=target,
                               content=content, msg_type="text")
        await router.forward_to_agent(target, base_msg)
    elif target.startswith("user:"):
        base_msg["type"] = "message"
        db.save_global_message(from_agent=f"user:{source}", to_target=target,
                               content=content, msg_type="text")
        ws_server.push_to_user(target[5:], base_msg)
    elif target == "broadcast:agents":
        db.save_global_message(from_agent=f"user:{source}", to_target=target,
                               content=content, msg_type="text")
        await router.broadcast_to_agents(base_msg)
    elif target == "broadcast:users":
        base_msg["type"] = "message"
        db.save_global_message(from_agent=f"user:{source}", to_target=target,
                               content=content, msg_type="text")
        ws_server.broadcast_to_users(base_msg)
    else:
        base_msg["type"] = "message"
        ws_server.push_to_channel("system", base_msg)

ws_server._on_incoming_chat = _on_incoming_chat

# 心跳兜底：孤儿输出通过 MessageRouter 路由
def _on_drain_outputs(agent_id: str, outputs: list):
    for output in outputs:
        try:
            asyncio.run(router._route_action(output))
        except RuntimeError:
            try:
                loop = asyncio.get_event_loop()
                asyncio.ensure_future(router._route_action(output))
            except Exception:
                pass
pm.on_drain_outputs = _on_drain_outputs


# ── FastAPI App ──

start_time = time.time()

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Main process starting...")
    _start_tunnel()
    pm.auto_start_agents()
    yield
    logger.info("Shutting down main process...")
    pm.cleanup()
    _stop_tunnel()
    logger.info("Main process stopped")

app = FastAPI(
    title="AHI-Multi v3.0",
    description="Multi Digital Life Support System",
    version="3.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_api_websocket_route("/ws", ws_server.get_ws_handler())


# ── WebSocket ──

@app.get("/ws/info")
async def websocket_docs():
    return {"message": "WebSocket endpoint available at ws://localhost:8000/ws"}


# ── Agent Registration & Notification ──

@app.post("/api/v1/agent/register")
def agent_register(data: Dict[str, Any]):
    agent_id = data.get("agent_id")
    if not agent_id:
        return {"code": 400, "message": "missing agent_id"}
    db.update_state(agent_id, {
        "pid": data.get("pid", 0),
        "status": "online",
        "port": data.get("agent_port", 0),
        "cpu_usage": data.get("cpu_usage", 0.0),
        "memory_usage": data.get("memory_usage", 0.0),
    })
    ws_server.broadcast_agent_online(agent_id)
    db.add_log("INFO", "System", f"Agent {agent_id} registered")
    return {"code": 200, "message": "registered", "agent_id": agent_id}


@app.post("/api/v1/agent/notify")
async def agent_notify(data: Dict[str, Any]):
    return await router.handle_notification(data)


# ── Messages ──

@app.post("/api/v1/messages")
async def post_message(data: Dict[str, Any]):
    from_agent = data.get("from", "")
    to = data.get("to", "")
    content = data.get("content", "")
    msg_type = data.get("type", "text")

    if not from_agent:
        return {"code": 400, "message": "missing 'from' field"}

    msg_id = db.save_global_message(
        from_agent=from_agent,
        to_target=to,
        content=content,
        msg_type=msg_type,
    )

    # 统一 JSON 消息体
    ts = time.time()
    target_type = to.split(":")[0] if ":" in to else "unknown"
    target_name = to.split(":", 1)[1] if ":" in to else to
    source_type = from_agent.split(":")[0] if ":" in from_agent else "unknown"
    source_name = from_agent.split(":", 1)[1] if ":" in from_agent else from_agent

    base_msg = {
        "type": msg_type,
        "from": from_agent,
        "from_agent": from_agent,
        "to": to,
        "content": content,
        "source": from_agent,
        "timestamp": ts,
        "msg_id": msg_id,
        "data": {"content": content},
        "metadata": {
            "msg_id": msg_id,
            "timestamp": ts,
            "source": from_agent,
            "source_type": source_type,
            "source_name": source_name,
            "target": to,
            "target_type": target_type,
            "target_name": target_name,
        },
        "payload": {"content": content},
    }

    if to.startswith("agent:"):
        base_msg["conversation_id"] = "default"
        forwarded = await router.forward_to_agent(to, base_msg)
        return {"code": 200, "message": "sent", "msg_id": msg_id, "forwarded": forwarded}

    if to.startswith("user:"):
        base_msg["type"] = "message"
        ws_server.push_to_user(to[5:], base_msg)
    elif to == "broadcast:agents":
        await router.broadcast_to_agents(base_msg)
    elif to == "broadcast:users":
        base_msg["type"] = "message"
        ws_server.broadcast_to_users(base_msg)
    else:
        base_msg["type"] = "message"
        ws_server.push_to_channel("system", base_msg)

    return {"code": 200, "message": "sent", "msg_id": msg_id}


@app.get("/api/v1/messages")
def get_messages(agent_id: Optional[str] = None,
                 limit: int = 50, offset: int = 0,
                 msg_type: Optional[str] = None):
    messages = db.get_global_messages(agent_id=agent_id, limit=limit, offset=offset,
                                      msg_type=msg_type)
    return {"code": 200, "data": messages}


# ── Agent Management ──

@app.get("/api/v1/agents")
def get_agents():
    agents = db.get_all_agents()
    return {"code": 200, "data": agents}


@app.get("/api/v1/agents/online")
def get_online_agents():
    agents = pm.get_online_agents()
    return {"code": 200, "data": agents}


@app.get("/api/v1/agents/{agent_id}")
def get_agent(agent_id: str):
    info = pm.get_agent_info(agent_id)
    if info:
        return {"code": 200, "data": info}
    return {"code": 404, "message": "Agent not found"}


@app.post("/api/v1/agents/{agent_id}/start")
def start_agent(agent_id: str):
    success = pm.start_agent(agent_id)
    if success:
        return {"code": 200, "message": "Agent starting"}
    return {"code": 500, "message": "Failed to start agent"}


@app.post("/api/v1/agents/{agent_id}/stop")
def stop_agent(agent_id: str, level: int = 1):
    success = pm.stop_agent(agent_id, level=level)
    if success:
        return {"code": 200, "message": "Agent stopped"}
    return {"code": 500, "message": "Failed to stop agent"}


@app.post("/api/v1/agents/{agent_id}/shell/reset")
def shell_reset(agent_id: str):
    agent = pm.get_agent(agent_id)
    if not agent:
        return {"code": 404, "message": "Agent not found"}
    state = db.get_agent_state(agent_id)
    if not state or state.get("status") != "online":
        return {"code": 400, "message": "Agent not online"}
    port = state.get("port", 0)
    try:
        result = pm._http_post(f"http://127.0.0.1:{port}/api/shell/reset", timeout=5)
        if result:
            return result
        return {"code": 500, "message": "Shell reset failed"}
    except Exception as e:
        return {"code": 500, "message": str(e)}


@app.post("/api/v1/agents/{agent_id}/loop/start")
def loop_start(agent_id: str):
    state = db.get_agent_state(agent_id)
    if not state or state.get("status") != "online":
        return {"code": 400, "message": "Agent not online"}
    port = state.get("port", 0)
    try:
        result = pm._http_post(f"http://127.0.0.1:{port}/api/loop/start", timeout=5)
        if result:
            return result
        return {"code": 500, "message": "Loop start failed"}
    except Exception as e:
        return {"code": 500, "message": str(e)}


@app.post("/api/v1/agents/{agent_id}/loop/pause")
def loop_pause(agent_id: str):
    state = db.get_agent_state(agent_id)
    if not state or state.get("status") != "online":
        return {"code": 400, "message": "Agent not online"}
    port = state.get("port", 0)
    try:
        result = pm._http_post(f"http://127.0.0.1:{port}/api/loop/pause", timeout=5)
        if result:
            return result
        return {"code": 500, "message": "Loop pause failed"}
    except Exception as e:
        return {"code": 500, "message": str(e)}


@app.post("/api/v1/agents/{agent_id}/loop/resume")
def loop_resume(agent_id: str):
    state = db.get_agent_state(agent_id)
    if not state or state.get("status") != "online":
        return {"code": 400, "message": "Agent not online"}
    port = state.get("port", 0)
    try:
        result = pm._http_post(f"http://127.0.0.1:{port}/api/loop/resume", timeout=5)
        if result:
            return result
        return {"code": 500, "message": "Loop resume failed"}
    except Exception as e:
        return {"code": 500, "message": str(e)}


@app.put("/api/v1/agents/{agent_id}/loop/interval")
def loop_interval(agent_id: str, seconds: int = 10):
    state = db.get_agent_state(agent_id)
    if not state or state.get("status") != "online":
        return {"code": 400, "message": "Agent not online"}
    port = state.get("port", 0)
    try:
        result = pm._http_put(f"http://127.0.0.1:{port}/api/config", timeout=5,
                              json_data={"wakeup_interval": seconds})
        if result:
            return result
        return {"code": 500, "message": "Interval update failed"}
    except Exception as e:
        return {"code": 500, "message": str(e)}


# ── System ──

@app.post("/api/v1/emergency-stop")
def emergency_stop():
    pm.emergency_stop_all()
    ws_server.broadcast_system_notification("error", "Global emergency stop triggered")
    return {"code": 200, "message": "Emergency stop initiated"}


@app.get("/api/v1/status")
def get_status():
    agents = db.get_all_agents()
    online = pm.get_online_agents()
    return {
        "code": 200,
        "data": {
            "uptime": time.time() - start_time,
            "total_agents": len(agents),
            "online_agents": len(online),
            "online_users": len(ws_server.get_connected_users()),
            "tunnel": {
                "name": config.get("cloudflare_tunnel_name") or None,
                "connected": _tunnel_connected,
                "url": _tunnel_url or None,
                "pid": _tunnel_process.pid if _tunnel_process else None,
                "recent_logs": list(_tunnel_log),
            },
        },
    }


@app.get("/api/v1/logs")
def get_logs(limit: int = 100):
    logs = db.get_recent_logs(limit)
    return {"code": 200, "data": logs}


@app.get("/health")
def health():
    return {"status": "healthy", "agents_count": pm.get_agent_count()}


# ── Static Files (must be last) ──

frontend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
if os.path.isdir(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")


# ── Entry Point ──

if __name__ == "__main__":
    import uvicorn
    port = config.get("main_process_port", 8000)
    logger.info("Starting AHI-Multi v3.0 main process on port %d", port)
    uvicorn.run("main:app", host="127.0.0.1", port=port, reload=False)
