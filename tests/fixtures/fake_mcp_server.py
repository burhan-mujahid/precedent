"""A tiny upstream MCP server for proxy tests. Logs every tools/call it receives to argv[1]."""

import json
import sys

LOG = sys.argv[1]
TOOLS = [
    {"name": "lookup_order", "description": "Read an order.", "inputSchema": {"type": "object", "properties": {
        "order": {"type": "string"}}, "required": ["order"]}},
    {"name": "issue_refund", "description": "Refund an order.", "inputSchema": {"type": "object", "properties": {
        "order": {"type": "string"}, "amount": {"type": "number"}}, "required": ["order", "amount"]}},
    {"name": "send_reply", "description": "Message a customer.", "inputSchema": {"type": "object", "properties": {
        "order": {"type": "string"}, "message": {"type": "string"}}}},
]

print("upstream log noise, not JSON-RPC", flush=True)
for line in sys.stdin:
    msg = json.loads(line)
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        continue
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake", "version": "1"}, "instructions": "Fake shop server."}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg["params"]) + "\n")
        result = {"content": [{"type": "text", "text": "done"}], "isError": False}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "nope"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}), flush=True)
