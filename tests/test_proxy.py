"""precedent proxy: another MCP server's tools behind the execution gate."""

import io
import json
import sys
from pathlib import Path

from precedent.proxy import Proxy

FAKE = Path(__file__).parent / "fixtures" / "fake_mcp_server.py"
ALLERGY = "Order 1182: is the walnut brownie safe for a nut allergy?"


def _run(kb, tmp_path, calls, **kw):
    log = tmp_path / "upstream.jsonl"
    proxy = Proxy(kb, [sys.executable, "-I", str(FAKE), str(log)], **kw)
    msgs = [{"jsonrpc": "2.0", "id": i, "method": m, "params": p} for i, (m, p) in enumerate(calls, 1)]
    out = io.StringIO()
    proxy.serve(io.StringIO("".join(json.dumps(m) + "\n" for m in msgs)), out)
    replies = {r["id"]: r for r in map(json.loads, out.getvalue().splitlines())}
    reached = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    return replies, reached


def _call(name, **args):
    return ("tools/call", {"name": name, "arguments": args})


def test_lists_upstream_tools_with_decision_id_and_own_tools(seeded, tmp_path):
    replies, _ = _run(seeded, tmp_path, [("initialize", {"protocolVersion": "2025-06-18"}), ("tools/list", {})],
                      pass_through=["lookup_order"])
    assert "consult_rulebook" in replies[1]["result"]["instructions"]
    assert "Fake shop server." in replies[1]["result"]["instructions"]
    tools = {t["name"]: t for t in replies[2]["result"]["tools"]}
    assert {"consult_rulebook", "check_action", "report_outcome"} <= set(tools)
    assert "decision_id" in tools["issue_refund"]["inputSchema"]["required"]
    assert "decision_id" not in tools["lookup_order"]["inputSchema"].get("properties", {})


def test_calls_without_a_current_decision_never_reach_upstream(seeded, tmp_path):
    d = seeded.resolve(ALLERGY, run_id="px-1")
    replies, reached = _run(seeded, tmp_path, [
        _call("lookup_order", order="1182"),                                        # pass-through
        _call("issue_refund", order="1182", amount=500),                            # no decision
        _call("send_reply", order="1182", message="The kitchen handles nuts.", decision_id="D-made-up"),
    ], pass_through=["lookup_order"])
    assert [r["name"] for r in reached] == ["lookup_order"]
    for i in (2, 3):
        res = replies[i]["result"]
        assert res["isError"] and res["structuredContent"]["outcome"] == "halt"
    assert {h.reason for h in seeded.store.halts()} == {"unknown_decision"}
    assert d.outcome == "answer"


def test_in_scope_call_is_forwarded_without_decision_id(seeded, tmp_path):
    d = seeded.resolve(ALLERGY, run_id="px-2")
    replies, reached = _run(seeded, tmp_path, [
        _call("send_reply", order="1182", message="The kitchen handles nuts.", decision_id=d.id),
        _call("issue_refund", order="1182", amount=500, decision_id=d.id),          # out of scope
        _call("send_reply", order="1182", message="Sorry!", decision_id=d.id),      # run already halted
    ])
    assert reached == [{"name": "send_reply", "arguments": {"order": "1182", "message": "The kitchen handles nuts."}}]
    assert not replies[1]["result"]["isError"]
    assert replies[2]["result"]["isError"] and "out_of_scope" in replies[2]["result"]["content"][0]["text"]
    assert replies[3]["result"]["isError"] and "already stopped" in replies[3]["result"]["content"][0]["text"]


def test_consult_through_the_proxy_then_final_tool_closes_the_decision(seeded, tmp_path):
    replies, reached = _run(seeded, tmp_path, [_call("consult_rulebook", situation=ALLERGY, run_id="px-3")])
    did = replies[1]["result"]["structuredContent"]["decision_id"]
    assert reached == []
    replies, reached = _run(seeded, tmp_path, [
        _call("send_reply", order="1182", message="The kitchen handles nuts.", decision_id=did),
        _call("send_reply", order="1182", message="The kitchen handles nuts.", decision_id=did),
    ], final=["send_reply"])
    assert len(reached) == 1 and not replies[1]["result"]["isError"]
    # sent before the first came back: refused while it is in flight, so it can't run twice
    assert replies[2]["result"]["isError"] and "in_flight" in replies[2]["result"]["content"][0]["text"]
    assert [e["stage"] for e in seeded.store.events("checked") if e["decision_id"] == did] == ["during", "after"]
    replies, reached = _run(seeded, tmp_path, [
        _call("send_reply", order="1182", message="The kitchen handles nuts.", decision_id=did)], final=["send_reply"])
    assert len(reached) == 1, "a later call is refused too: the decision was used up after Done"
    assert "already_done" in replies[1]["result"]["content"][0]["text"]
