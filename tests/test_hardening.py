"""Regression tests for edge cases found in review: one test per fix."""

import io
import json
import time
import urllib.request

import pytest

from precedent import Precedent, llm
from precedent.cli import main
from precedent.ingest import build_episodes, is_question, load_messages, redact
from precedent.ingest.jsonl import parse_jsonl
from precedent.ingest.whatsapp import parse_whatsapp
from precedent.llm import LLMError, LLMParseError, OpenAICompatibleClient
from precedent.loop import mine
from precedent.reasoner import LLMReasoner, OfflineReasoner, normalize_decision
from precedent.retrieve import BM25Retriever, stem, tokens
from precedent.store import Store, StoreError

from .conftest import EXAMPLE, add_rule

QA = "09/11/2026, 10:49 - Sara: How do we handle refunds?\n09/11/2026, 10:50 - Ali: Full refund within 7 days.\n"


# ----------------------------------------------------------------------------- WhatsApp input

def test_whatsapp_bom_keeps_first_message():
    assert len(parse_whatsapp("﻿" + QA)) == 2


def test_whatsapp_windows_line_endings():
    msgs = parse_whatsapp(QA.replace("\n", "\r\n"))
    assert [m.text for m in msgs] == ["How do we handle refunds?", "Full refund within 7 days."]


def test_whatsapp_rtl_bidi_marks_are_stripped():
    text = "‏09/11/2026, 10:49 - ‫علی‬: ‏سلام\n‎09/11/2026, 10:50 - Sara: hi\n"
    msgs = parse_whatsapp(text)
    assert [(m.author, m.text) for m in msgs] == [("علی", "سلام"), ("Sara", "hi")]


def test_load_messages_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_messages(tmp_path / "nope.txt")


# ----------------------------------------------------------------------------- Urdu / question detection

URDU = ("09/11/2026, 10:49 - سارہ: اگر کیک خراب پہنچے تو ہم کیا کرتے ہیں؟\n"
        "09/11/2026, 10:52 - علی: تصویر مانگیں، پھر مفت دوبارہ بنائیں یا پوری رقم واپس کریں۔\n"
        "09/11/2026, 10:53 - سارہ: شکریہ\n")


def test_urdu_script_question_becomes_an_episode():
    eps = build_episodes(parse_whatsapp(URDU))
    assert len(eps) == 1 and len(eps[0].replies) == 1


def test_roman_urdu_question_detected():
    assert is_question("agar cake kharab pohanche to hum kya karte hain?")
    assert is_question("kya hum 20 cupcakes par discount de sakte hain?")
    assert not is_question("kya scene hai aaj?")


def test_urdu_rules_are_retrievable(kb):
    rid = add_rule(kb, title="خراب کیک", when="اگر کیک خراب پہنچے", then="تصویر مانگیں اور مفت دوبارہ بنائیں",
                   risk="medium")
    assert tokens("اگر کیک خراب پہنچے تو؟")
    d = kb.resolve("گاہک کہتا ہے کیک خراب پہنچا", dry_run=True)
    assert rid in d.considered


def test_accented_latin_words_stay_whole():
    assert tokens("Devolución del pedido") == ["devolución", "del", "pedido"]


def test_chitchat_words_do_not_veto_business_questions():
    assert is_question("How do we handle a birthday cake order with the wrong name?")
    assert not is_question("should we get coffee before the meeting?")


# ----------------------------------------------------------------------------- retrieval

def test_stemmer_undoubles_and_drops_silent_e():
    assert stem("running") == stem("run") and stem("shipped") == stem("ship")
    assert stem("damage") == stem("damaged") == stem("damages")
    assert stem("cake") == stem("cakes") and stem("change") == stem("changed")
    assert stem("added") == stem("add") and stem("called") == stem("call") and stem("staffed") == stem("staff")


def test_matched_terms_are_the_callers_words(seeded):
    hits = BM25Retriever(seeded.store.rules("approved")).search("A delivery is running late")
    assert "running" in hits[0].matched and "runn" not in hits[0].matched


# ----------------------------------------------------------------------------- redaction

def test_redaction_keeps_dates_and_short_numbers():
    out = redact("Deliver on 2026-11-09 or 09-11-2026, order 12345678, call 0300-1234567")
    assert "2026-11-09" in out and "09-11-2026" in out and "12345678" in out
    assert "1234567" not in out.split("call")[1]


# ----------------------------------------------------------------------------- scale

def test_episode_building_is_linear_on_big_exports():
    lines = []
    for i in range(120_000):
        txt = "How do we handle refunds for late orders?" if i % 50 == 0 else "ok sounds good"
        lines.append(f"{1 + (i // 6000) % 28:02d}/10/2026, {(i // 300) % 24:02d}:{i % 60:02d} - U{i % 7}: {txt}")
    msgs = parse_whatsapp("\n".join(lines))
    t = time.perf_counter()
    eps = build_episodes(msgs)
    assert eps and time.perf_counter() - t < 6, "episode building should be roughly linear in export size"


# ----------------------------------------------------------------------------- hand-edited files

def test_empty_rule_file_names_the_file(kb):
    (kb.store.rules_dir / "R-099.yaml").write_text("")
    with pytest.raises(StoreError, match="R-099.yaml.*mapping"):
        kb.store.rules()


def test_unquoted_yaml_dates_load(seeded):
    path = seeded.store.rules_dir / "R-001.yaml"
    path.write_text(path.read_text().replace(next(ln for ln in path.read_text().splitlines()
                                                  if ln.startswith("review_by")), "review_by: 2020-01-01"))
    rule = seeded.store.rule("R-001")
    assert rule.review_by == "2020-01-01" and rule.is_stale()
    assert seeded.store.rules_as_of("2099-01-01")


def test_invalid_rule_date_is_a_clear_error(seeded):
    path = seeded.store.rules_dir / "R-001.yaml"
    path.write_text(path.read_text().replace("review_by: '", "review_by: 'soon"))
    with pytest.raises(StoreError, match="review_by must be a date"):
        seeded.store.rule("R-001")


def test_malformed_config_is_a_clear_error(kb):
    (kb.store.root / "precedent.yaml").write_text("provider: [\n")
    with pytest.raises(StoreError, match="precedent.yaml"):
        Store(kb.store.root)


def test_non_object_log_lines_are_skipped(seeded):
    seeded.resolve("something nobody wrote a rule for, about tax")
    with (seeded.store.logs_dir / "events.jsonl").open("a") as fh:
        fh.write("[1, 2]\n")
    assert len(seeded.store.gaps()) == 1


# ----------------------------------------------------------------------------- CLI errors

def test_cli_missing_key_is_one_line(kb, monkeypatch, capsys):
    cfg = kb.store.root / "precedent.yaml"
    cfg.write_text(cfg.read_text().replace("kind: offline", "kind: gemini"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert main(["ask", "refund", "--root", str(kb.store.root)]) == 1
    assert "error: GEMINI_API_KEY is not set" in capsys.readouterr().err


def test_cli_missing_ingest_file(kb, capsys):
    assert main(["ingest", "nope.txt", "--root", str(kb.store.root)]) == 1
    assert "nope.txt" in capsys.readouterr().err


# ----------------------------------------------------------------------------- model replies

class FakeClient:
    name, model = "fake", "m"

    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0

    def complete(self, system, user, max_tokens=None):
        self.calls += 1
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_json_repair_only_on_parse_errors():
    c = FakeClient(["sure! here you go", '{"rules": []}'])
    assert LLMReasoner(c)._json("s", "u") == {"rules": []} and c.calls == 2
    c = FakeClient([LLMError("HTTP 401", 401, "bad key")])
    with pytest.raises(LLMError):
        LLMReasoner(c)._json("s", "u")
    assert c.calls == 1, "an auth error must not be retried as a JSON repair"


def test_parse_failure_is_a_parse_error():
    with pytest.raises(LLMParseError):
        llm.parse_json("no json here")


def test_decision_with_bare_string_rule_id(seeded):
    rid = seeded.store.rules("approved")[0].id
    c = FakeClient([json.dumps({"status": "Covered", "answer": "Do it.", "rule_ids": rid, "confidence": 0.95})])
    d = Precedent(seeded.store, LLMReasoner(c)).resolve("A delivery is running more than an hour late", dry_run=True)
    assert d.outcome == "answer" and d.cited[0]["id"] == rid


def test_normalize_decision_shapes():
    assert normalize_decision({"rule_ids": [{"id": "R-1"}, 2, "", None]})["rule_ids"] == ["R-1", "2"]
    assert normalize_decision("garbage")["status"] == "not_covered"
    assert normalize_decision({"status": "Not Covered"})["status"] == "not_covered"


def _fake_urlopen(body: bytes):
    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    return lambda req, timeout=None, context=None: Resp(body)


def test_non_json_http_body_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(b"<html>proxy login</html>"))
    with pytest.raises(LLMError, match="non-JSON"):
        OpenAICompatibleClient(model="m", max_retries=0).complete("s", "u")


def test_max_completion_tokens_switch(monkeypatch):
    calls = []

    def fake_post(url, headers, body, timeout, retries):
        calls.append(body)
        if "max_tokens" in body:
            raise LLMError("HTTP 400", 400, "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead.")
        return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}

    monkeypatch.setattr(llm, "_post", fake_post)
    c = OpenAICompatibleClient(model="m", temperature=None)
    assert c.complete("s", "u") == "ok" and calls[-1]["max_completion_tokens"] == 2000
    c.complete("s", "u")
    assert len(calls) == 3


# ----------------------------------------------------------------------------- mining

def _episodes():
    return build_episodes(load_messages(EXAMPLE))


class SlowFailingClient(FakeClient):
    def complete(self, system, user, max_tokens=None):
        time.sleep(0.05)
        return super().complete(system, user, max_tokens)


def test_mining_stops_on_auth_errors(kb):
    eps = _episodes()
    c = SlowFailingClient([LLMError("HTTP 401", 401, "bad key")] * 20)
    with pytest.raises(LLMError):
        mine(kb.store, LLMReasoner(c), eps)
    assert c.calls < len(eps), "remaining episodes are cancelled"
    kb.store.config.data["ingest"]["workers"] = 1
    kb.store.episodes_path.unlink()
    c = FakeClient([LLMError("HTTP 401", 401, "bad key")] * 20)
    with pytest.raises(LLMError):
        mine(kb.store, LLMReasoner(c), eps)
    assert c.calls == 1


def test_mining_keeps_chat_order_with_parallel_workers(kb):
    eps = _episodes()

    class Echo:
        name = "echo"

        def extract(self, ep):
            time.sleep(0.02 * (len(eps) - eps.index(ep)))   # later episodes finish first
            return [{"when": ep.question.text, "then": "do it", "evidence": [ep.question.id]}]

        def decide(self, s, h):
            return {}

        def from_notes(self, n, r):
            return []

    mine(kb.store, Echo(), eps)
    assert [p.rule["when"] for p in kb.store.proposals("pending")] == [e.question.text for e in eps]


def test_model_risk_cannot_go_below_keyword_floor(kb):
    eps = _episodes()
    allergy = next(e for e in eps if "allergy" in e.question.text)
    reply = {"rules": [{"when": "A customer asks about nut allergies", "then": "Say the kitchen handles nuts.",
                        "risk": "low", "evidence": [allergy.question.id]}]}
    c = FakeClient([json.dumps(reply)])
    mine(kb.store, LLMReasoner(c), [allergy])
    assert kb.store.proposals("pending")[0].rule["risk"] == "high"


def test_mining_survives_malformed_candidates(kb):
    eps = _episodes()
    bad = [json.dumps({"rules": ["just a string", {"when": "x", "then": "y", "evidence": eps[0].question.id}]})]
    c = FakeClient(bad + ['{"rules": []}'] * (len(eps) - 1))
    rep = mine(kb.store, LLMReasoner(c), eps)
    assert rep.proposed == 1 and rep.ungrounded == 1, "string evidence accepted, non-dict candidate dropped"


def test_reingesting_skips_already_mined_episodes(kb):
    first = kb.ingest(EXAMPLE)
    c = FakeClient([])
    again = Precedent(kb.store, LLMReasoner(c)).ingest(EXAMPLE)
    assert again.already_mined == first.episodes and again.episodes == 0 and c.calls == 0


# ----------------------------------------------------------------------------- JSONL

def test_jsonl_timezones_and_epochs(tmp_path):
    f = tmp_path / "m.jsonl"
    f.write_text('{"ts": "2026-09-01T10:00:00Z", "author": "A", "text": "a"}\n'
                 '{"ts": "2026-09-01T12:30:00+05:00", "author": "B", "text": "b"}\n'
                 '{"ts": 1788256800, "author": "C", "text": "c"}\n')
    assert [m.ts for m in parse_jsonl(f)] == ["2026-09-01T07:30:00", "2026-09-01T10:00:00", "2026-09-01T10:00:00"]


def test_jsonl_bad_line_names_the_line(tmp_path):
    f = tmp_path / "m.jsonl"
    f.write_text('{"ts": "2026-09-01T10:00:00", "text": "ok"}\n{"author": "B"}\n')
    with pytest.raises(ValueError, match="line 2"):
        parse_jsonl(f)


# ----------------------------------------------------------------------------- library use

def test_two_rulebooks_keep_their_own_risk_terms(tmp_path):
    a = Precedent.init(tmp_path / "a", pack="support")
    b = Precedent.init(tmp_path / "b", pack="general")
    a, b = Precedent.open(a.store.root), Precedent.open(b.store.root)
    assert isinstance(a.reasoner, OfflineReasoner) and "chargeback" in a.reasoner.high_terms
    assert "chargeback" not in b.reasoner.high_terms


def test_context_of_any_type(seeded):
    d = seeded.resolve("A delivery is running late", context=["order 42", "2 hours"], dry_run=True)
    assert "order 42" in d.situation


def test_drafts_are_reviewed_in_chat_order(kb):
    kb.ingest(EXAMPLE)
    whens = [p.rule["when"] for p in kb.store.proposals("pending")]
    assert whens[0].lower().startswith("handling a customer who says their cake arrived damaged")
    assert "cancel" in whens[-1].lower()


def test_update_proposal_without_target_is_a_clear_error(seeded):
    from precedent.models import Proposal, new_id
    p = Proposal(id=new_id("P"), kind="update", rule={"then": "x"})
    seeded.store.save_proposal(p)
    with pytest.raises(StoreError, match="no target rule"):
        seeded.approve(p.id, "tester")


def test_cli_prints_urdu_on_a_legacy_windows_console(tmp_path):
    import os
    import subprocess
    import sys
    Precedent.init(tmp_path / "kb", name="کیک گھر")
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    out = subprocess.run([sys.executable, "-m", "precedent.cli", "export", "json", "--root", str(tmp_path / "kb")],
                         env=env, capture_output=True)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    assert "کیک گھر" in out.stdout.decode("utf-8")


def test_tls_certificate_failure_is_explained_and_not_retried(monkeypatch):
    import ssl
    import urllib.error
    calls = []

    def boom(req, timeout=None, context=None):
        calls.append(1)
        raise urllib.error.URLError(ssl.SSLCertVerificationError("unable to get local issuer certificate"))

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(LLMError, match="Install Certificates"):
        OpenAICompatibleClient(model="m", base_url="https://example.invalid/v1").complete("s", "u")
    assert len(calls) == 1


def test_dropping_a_rejected_param_is_thread_safe(monkeypatch):
    import threading as th
    barrier = th.Barrier(4)

    def fake_post(url, headers, body, timeout, retries):
        if "temperature" in body:
            barrier.wait(timeout=5)          # all four threads hit the 400 at the same time
            raise LLMError("HTTP 400", 400, '{"error": "invalid temperature: only 1 is allowed for this model"}')
        return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}

    monkeypatch.setattr(llm, "_post", fake_post)
    c = OpenAICompatibleClient(model="m", temperature=0)
    results, errors = [], []

    def call():
        try:
            results.append(c.complete("s", "u"))
        except Exception as exc:
            errors.append(exc)

    threads = [th.Thread(target=call) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and results == ["ok"] * 4


def test_usage_is_counted(monkeypatch):
    reply = {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 3}}
    monkeypatch.setattr(llm, "_post", lambda *a: reply)
    c = OpenAICompatibleClient(model="m")
    c.complete("s", "u")
    c.complete("s", "u")
    assert (c.usage.calls, c.usage.input_tokens, c.usage.output_tokens) == (2, 20, 6)
