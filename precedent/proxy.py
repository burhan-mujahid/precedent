"""An MCP proxy that puts the execution gate in front of another MCP server's tools.

    precedent proxy --root ./my-rulebook -- npx -y @acme/payments-mcp

Point your MCP client at the proxy instead of the upstream server:

    {"mcpServers": {"payments": {"command": "precedent",
                                 "args": ["proxy", "--root", "/path/to/rulebook", "--pass", "list_orders",
                                          "--", "npx", "-y", "@acme/payments-mcp"]}}}

What the client sees:

* the upstream server's tools, each with a required `decision_id` argument added;
* Precedent's own consult_rulebook, check_action and report_outcome, so the agent can get one.

What happens on each tools/call:

* Precedent's own tools are answered here.
* Tools named with --pass (or `boundary.pass_through` in precedent.yaml) go straight through. Use
  it for read-only lookups only.
* Every other tool goes through the execution gate first, with a step built from its real name and
  arguments. On `halt` the call never reaches the upstream server; the client gets the halt as an
  error result. On `continue` the call is forwarded without `decision_id`.
* Tools named with --final (or `boundary.final`) finish an action: after the upstream result comes
  back, the after-Done check runs, and the decision cannot be used again.

Everything else (notifications, resources, prompts, the upstream server's own requests to the
client) is passed through unchanged. The proxy never sends requests of its own, so message ids
never collide.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from collections.abc import Iterable, Sequence
from typing import Any, TextIO

from .boundary import DECISION_ARG, Blocked, gate, require_decision
from .guard import Check
from .kb import AGENT_INSTRUCTIONS, Precedent
from .mcp import Server, _tools
from .models import HALT

OWN_TOOLS = ("consult_rulebook", "check_action", "report_outcome")


def _key(mid: Any) -> str:
    return json.dumps(mid, sort_keys=True)


class Proxy:
    def __init__(self, kb: Precedent, upstream: Sequence[str], *, pass_through: Iterable[str] = (),
                 final: Iterable[str] = ()):
        if not upstream:
            raise ValueError("give the upstream MCP server's command after --")
        cfg = kb.store.config
        self.kb, self.upstream_cmd = kb, list(upstream)
        self.pass_through = {*pass_through, *(cfg.get("boundary.pass_through") or [])}
        self.final = {*final, *(cfg.get("boundary.final") or [])}
        self.local = Server(kb)
        self.own = [t for t in _tools() if t["name"] in OWN_TOOLS]
        self._pending: dict[str, tuple[str, Any]] = {}   # request id -> what to do with its response
        self._lock = threading.Lock()
        self._out_lock = threading.Lock()
        self._gate_lock = threading.Lock()   # both directions write to the same rulebook logs
        self._proc: subprocess.Popen[str] | None = None
        self._out: TextIO = sys.stdout

    # ------------------------------------------------------------------ plumbing
    def _send_client(self, msg: dict[str, Any]) -> None:
        with self._out_lock:
            self._out.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self._out.flush()

    def _send_upstream(self, msg: dict[str, Any]) -> None:
        assert self._proc and self._proc.stdin
        try:
            self._proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            if msg.get("id") is not None and "method" in msg:
                self._send_client({"jsonrpc": "2.0", "id": msg["id"],
                                   "error": {"code": -32603, "message": "the upstream MCP server is not running"}})

    def _track(self, mid: Any, what: str, data: Any = None) -> None:
        with self._lock:
            self._pending[_key(mid)] = (what, data)

    def _in_flight(self, did: Any) -> bool:
        with self._lock:
            return any(w == "final" and d[2] == did for w, d in self._pending.values())

    def _untrack(self, mid: Any) -> tuple[str, Any] | None:
        with self._lock:
            return self._pending.pop(_key(mid), None)

    # ------------------------------------------------------------------ client -> upstream
    def from_client(self, msg: dict[str, Any]) -> None:
        method, mid = msg.get("method"), msg.get("id")
        if method == "tools/call" and mid is not None:
            params = msg.get("params") or {}
            name = str(params.get("name") or "")
            if name in OWN_TOOLS:
                with self._gate_lock:
                    reply = self.local.handle(msg)
                if reply is not None:
                    self._send_client(reply)
                return
            if name not in self.pass_through:
                args = dict(params.get("arguments") or {})
                did = args.pop(DECISION_ARG, None)
                with self._gate_lock:
                    if name in self.final and self._in_flight(did):
                        c = Check(HALT, str(did), "during", reasons=[
                            "in_flight: a final call on this decision is still running; one decision covers "
                            "one action"])
                    else:
                        c = gate(self.kb, name, args, did)
                    if name in self.final and not c.halted:   # claim it before the lock is released
                        self._track(mid, "final", (name, args, did))
                if c.halted:
                    blocked = Blocked(c, name)
                    self._send_client({"jsonrpc": "2.0", "id": mid, "result": {
                        "content": [{"type": "text", "text": str(blocked)}],
                        "structuredContent": blocked.to_dict(), "isError": True}})
                    return
                msg = {**msg, "params": {**params, "arguments": args}}
        elif method == "tools/list" and mid is not None:
            self._track(mid, "tools/list", not (msg.get("params") or {}).get("cursor"))
        elif method == "initialize" and mid is not None:
            self._track(mid, "initialize")
        self._send_upstream(msg)

    # ------------------------------------------------------------------ upstream -> client
    def from_upstream(self, msg: dict[str, Any]) -> dict[str, Any]:
        if "method" in msg or msg.get("id") is None:
            return msg                     # a notification or the upstream server's own request
        tracked = self._untrack(msg["id"])
        result = msg.get("result")
        if not tracked or not isinstance(result, dict):
            return msg
        what, data = tracked
        if what == "tools/list":
            tools = [t for t in result.get("tools") or [] if isinstance(t, dict) and t.get("name") not in OWN_TOOLS]
            tools = [t if t.get("name") in self.pass_through else require_decision(t) for t in tools]
            result = {**result, "tools": (self.own if data else []) + tools}
        elif what == "initialize":
            theirs = str(result.get("instructions") or "").strip()
            caps = dict(result.get("capabilities") or {})
            caps.setdefault("tools", {"listChanged": False})
            result = {**result, "capabilities": caps,
                      "instructions": AGENT_INSTRUCTIONS + " Every other tool here needs a decision_id from "
                      "consult_rulebook and is refused without a current one." + (f"\n\n{theirs}" if theirs else "")}
        elif what == "final" and not result.get("isError"):
            name, args, did = data
            with self._gate_lock:
                c = gate(self.kb, name, args, did, stage="after")
            if c.halted:
                blocked = Blocked(c, name)
                result = {**result, "content": [*(result.get("content") or []),
                                                 {"type": "text", "text": str(blocked)}], "isError": True}
        return {**msg, "result": result}

    # ------------------------------------------------------------------ run
    def _pump_upstream(self) -> None:
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue                   # not JSON-RPC: upstream log noise on stdout
            if isinstance(msg, dict):
                self._send_client(self.from_upstream(msg))

    def serve(self, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int:
        self._out = stdout
        self._proc = subprocess.Popen(self.upstream_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      text=True, encoding="utf-8", errors="replace", bufsize=1)
        reader = threading.Thread(target=self._pump_upstream, daemon=True)
        reader.start()
        try:
            for line in stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    if len(line) > 1_000_000:
                        raise ValueError("message too large")
                    msg = json.loads(line)
                    if not isinstance(msg, dict):
                        raise ValueError("expected a JSON object")
                except ValueError:
                    self._send_client({"jsonrpc": "2.0", "id": None, "error": {"code": -32700,
                                                                               "message": "parse error"}})
                    continue
                self.from_client(msg)
        finally:
            if self._proc.stdin:
                try:
                    self._proc.stdin.close()
                except OSError:
                    pass
            reader.join(timeout=10)
            try:
                code = self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.terminate()
                code = self._proc.wait()
        return code
