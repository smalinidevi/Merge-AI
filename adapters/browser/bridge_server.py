"""
Local HTTP JSON bridge for the Merge AI Chrome extension.

Listens on 127.0.0.1:8765. The extension POSTs tab state; Python adapters
GET/POST commands. Stdlib only — no external deps.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

_lock = threading.RLock()
_state: dict[str, Any] = {
    "connected": False,
    "active_tab": None,  # {id,url,title,text}
    "tabs": [],  # [{id,url,title}]
    "last_command": None,
    "last_result": None,
    "pending_command": None,  # command waiting for extension
}


def get_state() -> dict[str, Any]:
    with _lock:
        return json.loads(json.dumps(_state))


def reset_state() -> None:
    with _lock:
        _state["connected"] = False
        _state["active_tab"] = None
        _state["tabs"] = []
        _state["last_command"] = None
        _state["last_result"] = None
        _state["pending_command"] = None


def _json_response(handler: BaseHTTPRequestHandler, code: int, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


class BridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # quieter tests
        return

    def do_OPTIONS(self) -> None:  # noqa: N802
        _json_response(self, 200, {"ok": True})

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        with _lock:
            if path in ("/status", "/"):
                _json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "connected": _state["connected"],
                        "active_tab": _state["active_tab"],
                        "tabs": _state["tabs"],
                        "pending_command": _state["pending_command"],
                    },
                )
                return
            if path == "/get_active_tab":
                _json_response(
                    self,
                    200,
                    {"ok": True, "tab": _state["active_tab"], "connected": _state["connected"]},
                )
                return
            if path == "/list_tabs":
                _json_response(
                    self,
                    200,
                    {"ok": True, "tabs": _state["tabs"], "connected": _state["connected"]},
                )
                return
            if path == "/poll_command":
                # Extension polls for work
                cmd = _state["pending_command"]
                _json_response(self, 200, {"ok": True, "command": cmd})
                return
        _json_response(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        data = _read_json(self)
        with _lock:
            if path == "/extension/hello":
                _state["connected"] = True
                if isinstance(data.get("tabs"), list):
                    _state["tabs"] = data["tabs"]
                if isinstance(data.get("active_tab"), dict):
                    _state["active_tab"] = data["active_tab"]
                _json_response(self, 200, {"ok": True})
                return
            if path == "/extension/update":
                _state["connected"] = True
                if isinstance(data.get("tabs"), list):
                    _state["tabs"] = data["tabs"]
                if isinstance(data.get("active_tab"), dict):
                    _state["active_tab"] = data["active_tab"]
                _json_response(self, 200, {"ok": True})
                return
            if path == "/extension/command_result":
                _state["last_result"] = data
                _state["pending_command"] = None
                if isinstance(data.get("active_tab"), dict):
                    _state["active_tab"] = data["active_tab"]
                if isinstance(data.get("tabs"), list):
                    _state["tabs"] = data["tabs"]
                _json_response(self, 200, {"ok": True})
                return
            if path == "/navigate":
                url = str(data.get("url") or "")
                _state["pending_command"] = {"op": "navigate", "url": url}
                _state["last_command"] = _state["pending_command"]
                _json_response(self, 200, {"ok": True, "queued": True})
                return
            if path == "/google_search":
                query = str(data.get("query") or "")
                _state["pending_command"] = {"op": "google_search", "query": query}
                _state["last_command"] = _state["pending_command"]
                _json_response(self, 200, {"ok": True, "queued": True})
                return
            if path == "/activate_tab":
                tab_id = data.get("tab_id")
                _state["pending_command"] = {"op": "activate_tab", "tab_id": tab_id}
                _state["last_command"] = _state["pending_command"]
                _json_response(self, 200, {"ok": True, "queued": True})
                return
            if path == "/command":
                # generic enqueue
                _state["pending_command"] = data
                _state["last_command"] = data
                _json_response(self, 200, {"ok": True, "queued": True})
                return
        _json_response(self, 404, {"ok": False, "error": "not found"})


_server: Optional[ThreadingHTTPServer] = None
_thread: Optional[threading.Thread] = None


def start_bridge(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    global _server, _thread
    if _server is not None:
        return _server
    server = ThreadingHTTPServer((host, port), BridgeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="merge-ai-browser-bridge")
    thread.start()
    _server = server
    _thread = thread
    return server


def stop_bridge() -> None:
    global _server, _thread
    if _server is not None:
        _server.shutdown()
        _server.server_close()
    _server = None
    _thread = None
    reset_state()


def bridge_base_url(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> str:
    return f"http://{host}:{port}"


def http_json(method: str, url: str, payload: Optional[dict] = None, timeout: float = 2.0) -> dict:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"ok": False, "error": body}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def ensure_bridge(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> str:
    """Start bridge if needed; return base URL."""
    base = bridge_base_url(host, port)
    status = http_json("GET", f"{base}/status", timeout=0.4)
    if status.get("ok"):
        return base
    try:
        start_bridge(host, port)
    except OSError:
        # port busy — assume another instance
        pass
    return base
