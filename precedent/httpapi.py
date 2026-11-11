"""A small HTTP API over the same tools, for workflow tools that can't run a command step.

    precedent serve --root ./my-rulebook                 # http://127.0.0.1:8787
    PRECEDENT_HTTP_TOKEN=... precedent serve --host 0.0.0.0

    POST /consult   {"situation": "...", "run_id": "..."}            -> consult_rulebook
    POST /check     {"decision_id": "D-...", "step": "...", "stage"} -> check_action
    POST /outcome   {"decision_id": "D-...", "verdict": "halt", ...} -> report_outcome
    POST /tools/<name>                                               -> any MCP tool by name
    GET  /health

Replies are the same JSON the MCP tools return, and fail closed the same way: a consult that
errors comes back as escalate, a check that errors comes back as halt. n8n, Make and Zapier can
branch on `outcome`.

Standard library only. It binds to 127.0.0.1 by default. With PRECEDENT_HTTP_TOKEN set, every
request needs `Authorization: Bearer <token>`; it refuses to listen beyond localhost without one.
Requests are handled one at a time, because they all write to the same rulebook logs.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from . import __version__
from .kb import Precedent
from .mcp import Server

ROUTES = {"/consult": "consult_rulebook", "/check": "check_action", "/outcome": "report_outcome"}
MAX_BODY = 1_000_000


def _is_local(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def make_handler(server: Server, token: str | None) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = f"precedent/{__version__}"

        def log_message(self, format: str, *args: Any) -> None:   # requests carry situations: keep them out of logs
            pass

        def _reply(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorised(self) -> bool:
            if not token:
                return True
            got = self.headers.get("Authorization", "")
            return hmac.compare_digest(got.encode(), f"Bearer {token}".encode())

        def do_GET(self) -> None:
            if not self._authorised():
                return self._reply(401, {"error": "missing or wrong bearer token"})
            if self.path.split("?")[0] == "/health":
                return self._reply(200, {"ok": True, "version": __version__})
            self._reply(404, {"error": f"no such endpoint: GET {self.path}"})

        def do_POST(self) -> None:
            if not self._authorised():
                return self._reply(401, {"error": "missing or wrong bearer token"})
            path = self.path.split("?")[0].rstrip("/")
            tool = ROUTES.get(path) or (path[len("/tools/"):] if path.startswith("/tools/") else "")
            if not tool:
                return self._reply(404, {"error": f"no such endpoint: POST {self.path}"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY:
                return self._reply(413, {"error": f"body must be at most {MAX_BODY} bytes"})
            try:
                args = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(args, dict):
                    raise ValueError("expected a JSON object")
            except ValueError as exc:
                return self._reply(400, {"error": f"invalid JSON: {exc}"})
            try:
                payload = server.call(tool, args)
            except KeyError:
                return self._reply(404, {"error": f"unknown tool {tool}"})
            except Exception as exc:    # bad arguments for report_outcome, search_rules...
                return self._reply(400, {"error": f"{type(exc).__name__}: {exc}"})
            self._reply(200, payload)

    return Handler


def serve(kb: Precedent, host: str = "127.0.0.1", port: int = 8787, token: str | None = None) -> HTTPServer:
    """Build the server (call .serve_forever() on it). Refuses a non-local address without a token."""
    token = token if token is not None else os.environ.get("PRECEDENT_HTTP_TOKEN") or None
    if not token and not _is_local(host):
        raise ValueError(f"refusing to listen on {host} without a token: set PRECEDENT_HTTP_TOKEN, or use 127.0.0.1")
    return HTTPServer((host, port), make_handler(Server(kb, channel="http"), token))
