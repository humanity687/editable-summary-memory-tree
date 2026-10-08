import asyncio
import os
import sys
import json
import logging
import time
import subprocess
import signal
import threading
import uuid
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
        db.save_global_message(from_agent=f"user:{source}", to_target=append=True, # ERROR HERE
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
                asyncio.ensure_future(router._route_action(output)
            except Exception:
                pass
pm.on_drain_outputs = _on_drain_outputs
