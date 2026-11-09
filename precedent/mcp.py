"""MCP server over stdio, standard library only.

    precedent mcp --root ./my-rulebook

Add to any MCP client (Claude Desktop/Code, Cursor, Hermes, ...):

    {"mcpServers": {"precedent": {"command": "precedent", "args": ["mcp", "--root", "/path/to/rulebook"]}}}

Tools:
    consult_rulebook   the runtime gate: answer / needs_approval / escalate, with cited rule versions
    report_outcome     after the fact: confirm (becomes a regression test) or override (becomes a fix)
    search_rules       read-only lookup of approved rules
    open_gaps          what agents could not handle, for a human to answer
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from . import __version__
from .kb import AGENT_INSTRUCTIONS, Precedent
from .retrieve import BM25Retriever

SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")


def _tools() -> list[dict[str, Any]]:
    consult = Precedent.tool_spec()
    return [
        {"name": consult["name"], "description": consult["description"], "inputSchema": consult["input_schema"]},
        {"name": "report_outcome",
         "description": "Tell the rulebook whether a decision was right. 'confirm' turns it into a regression test; "
                        "'override' (with the correct action) logs a gap and drafts a rule fix for human review.",
         "inputSchema": {"type": "object", "properties": {
             "decision_id": {"type": "string"},
             "verdict": {"type": "string", "enum": ["confirm", "override"]},
             "correction": {"type": "string", "description": "Required for override: what should have happened."},
             "by": {"type": "string", "description": "Who is reporting (name or role)."}},
             "required": ["decision_id", "verdict"]}},
        {"name": "search_rules",
         "description": "Look up approved rules by keywords. Read-only; use consult_rulebook to decide actions.",
         "inputSchema": {"type": "object", "properties": {"query": {"type": "string"},
                                                          "limit": {"type": "integer", "default": 5}},
                         "required": ["query"]}},
        {"name": "open_gaps",
         "description": "List situations agents escalated that still need a human answer.",
         "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 20}}}},
    ]


def _limit(value: Any, default: int) -> int:
    try:
        return max(1, min(100, int(value if value is not None else default)))
    except (TypeError, ValueError):
        raise ValueError("limit must be an integer") from None


def _text(args: dict[str, Any], key: str, max_len: int = 4000) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' must be a non-empty string")
    if len(value) > max_len:
        raise ValueError(f"'{key}' is longer than {max_len} characters")
    return value


class Server:
    def __init__(self, kb: Precedent):
        self.kb = kb

    def call(self, name: str, args: dict[str, Any]) -> Any:
        if not isinstance(args, dict):
            raise ValueError("arguments must be an object")
        if name == "consult_rulebook":
            return self.kb.handle_tool_call(args)
        if name == "report_outcome":
            verdict = args.get("verdict")
            if verdict not in ("confirm", "override"):
                raise ValueError("verdict must be confirm or override")
            # Agents cannot vouch for themselves: whatever they claim, the audit trail says it came over MCP.
            by = "mcp:" + str(args.get("by") or "agent")[:80]
            correction = _text(args, "correction") if verdict == "override" else ""
            msg = self.kb.feedback(_text(args, "decision_id", 64), by=by,
                                   confirm=verdict == "confirm", correction=correction)
            return {"ok": True, "result": msg}
        if name == "search_rules":
            hits = BM25Retriever(self.kb.store.rules("approved")).search(_text(args, "query"),
                                                                         _limit(args.get("limit"), 5))
            return [{"id": h.rule.id, "version": h.rule.version, "title": h.rule.title, "when": h.rule.when,
                     "then": h.rule.then, "risk": h.rule.risk, "match": h.coverage} for h in hits]
        if name == "open_gaps":
            gaps = self.kb.store.gaps("open")[: _limit(args.get("limit"), 20)]
            return [{"id": g.id, "situation": g.situation, "reason": g.reason, "ts": g.ts} for g in gaps]
        raise KeyError(f"unknown tool {name}")

    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:          # notification (e.g. notifications/initialized): no reply
            return None
        try:
            if method == "initialize":
                asked = (msg.get("params") or {}).get("protocolVersion")
                result: Any = {
                    "protocolVersion": asked if asked in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0],
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "precedent", "version": __version__},
                    "instructions": AGENT_INSTRUCTIONS,
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": _tools()}
            elif method == "tools/call":
                params = msg.get("params") or {}
                try:
                    payload = self.call(params.get("name", ""), params.get("arguments") or {})
                    result = {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                              "structuredContent": payload if isinstance(payload, dict) else {"items": payload},
                              "isError": False}
                except Exception as exc:   # tool errors are results, not protocol errors
                    result = {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}], "isError": True}
            else:
                return {"jsonrpc": "2.0", "id": mid,
                        "error": {"code": -32601, "message": f"method not found: {method}"}}
            return {"jsonrpc": "2.0", "id": mid, "result": result}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(exc)}}

    def serve(self, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
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
            except ValueError:   # JSONDecodeError is a ValueError
                reply: dict[str, Any] | None = {"jsonrpc": "2.0", "id": None,
                                                "error": {"code": -32700, "message": "parse error"}}
            else:
                reply = self.handle(msg)
            if reply is not None:
                stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
                stdout.flush()
