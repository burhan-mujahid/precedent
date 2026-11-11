"""precedent serve: the same gates over HTTP."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from precedent.httpapi import serve

ALLERGY = "Order 1182: is the walnut brownie safe for a nut allergy?"


@pytest.fixture
def api(seeded):
    servers = []

    def start(token=None):
        httpd = serve(seeded, "127.0.0.1", 0, token=token or "")
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return f"http://127.0.0.1:{httpd.server_port}"

    yield start
    for s in servers:
        s.shutdown()
        s.server_close()


def _post(base, path, body, token=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_consult_check_and_halt_over_http(api, seeded):
    base = api()
    status, d = _post(base, "/consult", {"situation": ALLERGY, "run_id": "http-1"})
    assert status == 200 and d["outcome"] == "answer"
    status, c = _post(base, "/check", {"decision_id": d["decision_id"], "step": "Refund 500 on order 1182"})
    assert status == 200 and c["outcome"] == "halt"
    status, out = _post(base, "/outcome", {"decision_id": d["decision_id"], "verdict": "halt",
                                           "reason": "verifier: mismatch", "by": "observer"})
    assert status == 200 and out["ok"]
    assert seeded.store.halts()[-1].by == "http:observer"


def test_fails_closed_and_rejects_bad_requests(api):
    base = api()
    status, c = _post(base, "/check", {"decision_id": "D-nope", "step": "anything"})
    assert status == 200 and c["outcome"] == "halt"
    assert _post(base, "/nowhere", {})[0] == 404
    assert _post(base, "/tools/not_a_tool", {})[0] == 404
    assert _post(base, "/outcome", {"decision_id": "D-x", "verdict": "maybe"})[0] == 400


def test_token_is_required_when_set(api):
    base = api(token="s3cret")
    assert _post(base, "/consult", {"situation": ALLERGY})[0] == 401
    assert _post(base, "/consult", {"situation": ALLERGY}, token="wrong")[0] == 401
    assert _post(base, "/consult", {"situation": ALLERGY}, token="s3cret")[0] == 200


def test_refuses_public_bind_without_token(seeded, monkeypatch):
    monkeypatch.delenv("PRECEDENT_HTTP_TOKEN", raising=False)
    with pytest.raises(ValueError, match="without a token"):
        serve(seeded, "0.0.0.0", 0)
