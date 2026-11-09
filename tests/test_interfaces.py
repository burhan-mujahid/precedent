import io
import json

import pytest

from precedent.cli import main
from precedent.export import to_agents_md, to_parlant, to_skill
from precedent.llm import LLMError, parse_json
from precedent.mcp import Server
from precedent.reasoner import LLMReasoner
from precedent.retrieve import BM25Retriever

from .conftest import EXAMPLE


def test_parse_json_tolerates_fences_and_chatter():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure! Here you go: {"a": {"b": "}"}} hope that helps') == {"a": {"b": "}"}}
    with pytest.raises(LLMError):
        parse_json("no json here")


class FakeClient:
    name, model = "fake", "m"

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def complete(self, system, user, max_tokens=2000):
        self.prompts.append(user)
        return self.replies.pop(0)


def test_llm_reasoner_repairs_once_and_tempers_confidence(seeded):
    q = "delivery an hour late because of a storm"     # partial coverage
    hits = BM25Retriever(seeded.store.rules("approved")).search(q)
    client = FakeClient(["not json", json.dumps({"status": "covered", "answer": "go", "rule_ids": [hits[0].rule.id],
                                                 "confidence": 1.0})])
    out = LLMReasoner(client).decide(q, hits)
    assert out["status"] == "covered" and out["confidence"] < 1.0
    assert "<situation>" in client.prompts[0], "inputs are fenced as data"


def test_exports(seeded, tmp_path):
    skill = to_skill(seeded.store, tmp_path)
    md = (skill / "SKILL.md").read_text()
    assert md.startswith("---\nname: test-co-rulebook\ndescription: ")
    assert (skill / "references" / "rules.md").exists()
    assert "precedent:begin" in to_agents_md(seeded.store)
    compile(to_parlant(seeded.store), "load.py", "exec")


def _rpc(server, method, params=None, mid=1):
    return server.handle({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})


def test_mcp_server(seeded):
    s = Server(seeded)
    init = _rpc(s, "initialize", {"protocolVersion": "2025-06-18"})
    assert init["result"]["serverInfo"]["name"] == "precedent"
    assert s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    names = {t["name"] for t in _rpc(s, "tools/list")["result"]["tools"]}
    assert names == {"consult_rulebook", "report_outcome", "search_rules", "open_gaps"}
    res = _rpc(s, "tools/call", {"name": "consult_rulebook", "arguments": {"situation": "nut allergy brownie safe?"}})
    payload = json.loads(res["result"]["content"][0]["text"])
    assert payload["outcome"] == "answer"
    args = {"decision_id": payload["decision_id"], "verdict": "confirm"}
    fb = _rpc(s, "tools/call", {"name": "report_outcome", "arguments": args})
    assert fb["result"]["isError"] is False
    bad = _rpc(s, "tools/call", {"name": "nope", "arguments": {}})
    assert bad["result"]["isError"] is True
    assert _rpc(s, "bogus")["error"]["code"] == -32601


def test_mcp_stdio_loop(seeded):
    s = Server(seeded)
    out = io.StringIO()
    s.serve(io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\nnot json\n'), out)
    lines = [json.loads(x) for x in out.getvalue().splitlines()]
    assert lines[0]["result"] == {} and lines[1]["error"]["code"] == -32700


def test_cli_end_to_end(tmp_path, capsys, monkeypatch):
    root = str(tmp_path / "kb")
    monkeypatch.setenv("PRECEDENT_USER", "owner")
    assert main(["init", root, "--pack", "ops"]) == 0
    assert main(["ingest", str(EXAMPLE), "--root", root]) == 0
    capsys.readouterr()
    main(["review", "list", "--root", root])
    ids = [line.split()[0] for line in capsys.readouterr().out.splitlines()]
    for pid in ids:
        assert main(["review", "approve", pid, "--root", root]) == 0
    assert main(["ask", "a", "delivery", "is", "running", "an", "hour", "late", "--root", root]) == 0
    assert main(["ask", "should we open a second shop in Karachi?", "--root", root]) == 3
    assert main(["health", "--root", root, "--strict"]) == 0
    assert main(["eval", "--root", root]) == 0
    assert main(["export", "markdown", "--root", root]) == 0
    assert main(["review", "approve", "--root", root]) == 64
    assert main(["rules", "show", "R-999", "--root", root]) == 1
