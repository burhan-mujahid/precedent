import json

from precedent.ingest import build_episodes, is_question, load_messages, redact
from precedent.ingest.slack import parse_slack
from precedent.ingest.whatsapp import parse_whatsapp

from .conftest import EXAMPLE


def test_whatsapp_android_format_skips_system_and_media():
    msgs = load_messages(EXAMPLE)
    assert all(m.author for m in msgs)
    assert not any("omitted" in m.text.lower() for m in msgs)
    assert not any("created group" in m.text for m in msgs)
    assert msgs[0].ts.startswith("2026-09-02")


def test_whatsapp_ios_ampm_and_multiline():
    text = ("[9/14/26, 3:05:10 PM] Sara: How do we handle returns?\n"
            "second line of the question\n"
            "[9/14/26, 3:07:00 PM] Ali: We accept within 14 days.\n")
    msgs = parse_whatsapp(text, channel="t")
    assert len(msgs) == 2
    assert msgs[0].ts == "2026-09-14T15:05:10"      # month-first inferred from 14
    assert "second line" in msgs[0].text


def test_whatsapp_dayfirst_inference():
    msgs = parse_whatsapp("13/02/2026, 09:00 - A: hi\n01/03/2026, 09:00 - B: yo\n")
    assert msgs[1].ts.startswith("2026-03-01")


def test_redaction():
    out = redact("call +92 300 0000000 or mail ali@example.com, card 4111 1111 1111 1111")
    assert "0000000" not in out and "ali@example.com" not in out and "4111" not in out
    assert "[phone]" in out and "[email]" in out


def test_question_detection():
    assert is_question("How do we handle a damaged cake?")
    assert is_question("Can we give a discount for 20 cupcakes?")
    assert not is_question("anyone free for lunch?")
    assert not is_question("ok?")
    assert not is_question("We shipped it yesterday.")


def test_episodes_stop_at_ack_and_exclude_chitchat():
    eps = build_episodes(load_messages(EXAMPLE))
    assert len(eps) == 9
    damaged = next(e for e in eps if "damaged" in e.question.text)
    assert len(damaged.replies) == 1, "episode must end when the asker says 'ok thanks'"
    assert all("sourdough" not in r.text for e in eps for r in e.replies)


def test_episode_requires_answer_from_someone_else():
    text = "01/09/2026, 10:00 - A: How do we handle refunds?\n01/09/2026, 10:01 - A: anyone?\n"
    assert build_episodes(parse_whatsapp(text)) == []


def test_slack_export_threads(tmp_path):
    users = [{"id": "U1", "real_name": "Sara"}, {"id": "U2", "real_name": "Ali"}]
    (tmp_path / "users.json").write_text(json.dumps(users))
    chan = tmp_path / "ops"
    chan.mkdir()
    (chan / "2026-09-01.json").write_text(json.dumps([
        {"type": "message", "user": "U1", "text": "How do we handle a refund over $100?", "ts": "1756720000.0001",
         "thread_ts": "1756720000.0001"},
        {"type": "message", "user": "U2", "text": "Owner approves anything over $100, <@U1>", "ts": "1756720100.0001",
         "thread_ts": "1756720000.0001"},
        {"type": "message", "subtype": "channel_join", "user": "U2", "text": "joined", "ts": "1756720200.0"},
    ]))
    msgs = parse_slack(tmp_path)
    assert [m.author for m in msgs] == ["Sara", "Ali"]
    assert "@Sara" in msgs[1].text
    eps = build_episodes(msgs)
    assert len(eps) == 1 and eps[0].replies[0].author == "Ali"


def test_jsonl(tmp_path):
    f = tmp_path / "m.jsonl"
    f.write_text('{"ts": "2026-09-01T10:00:00", "author": "A", "text": "Should we reply to bad reviews?"}\n'
                 '{"ts": "2026-09-01T10:05:00", "author": "B", "text": "Always, within a day."}\n')
    assert len(build_episodes(load_messages(f))) == 1
