import pytest

from precedent import llm
from precedent.llm import LLMError, OpenAICompatibleClient, make_client, resolve_provider


def test_presets_fill_endpoint_and_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("MOONSHOT_API_KEY", "k")
    g = make_client({"kind": "gemini", "model": "gemini-x", "temperature": 0})
    assert g.base.endswith("/v1beta/openai") and g.api_key == "g" and g.max_tokens == 8000
    assert g.optional == {"temperature": 0, "reasoning_effort": "low"}
    k = make_client({"kind": "kimi", "model": "kimi-x"})
    assert k.base == "https://api.moonshot.ai/v1" and k.api_key == "k"
    assert resolve_provider({"kind": "kimi", "max_tokens": 999})["max_tokens"] == 999   # explicit wins


def test_missing_key_and_model_are_clear(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        make_client({"kind": "gemini", "model": "m"})
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.delenv("PRECEDENT_MODEL", raising=False)
    with pytest.raises(LLMError, match="precedent doctor"):
        make_client({"kind": "gemini"})
    assert make_client({"kind": "offline"}) is None


def test_rejected_optional_param_is_dropped_and_retried(monkeypatch):
    calls = []

    def fake_post(url, headers, body, timeout, retries):
        calls.append(body)
        if "temperature" in body:
            raise LLMError("HTTP 400", 400, '{"error": "invalid temperature: only 1 is allowed for this model"}')
        return {"choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}]}

    monkeypatch.setattr(llm, "_post", fake_post)
    c = OpenAICompatibleClient(model="m", api_key="k", temperature=0)
    assert c.complete("s", "u") == '{"ok": true}'
    assert "temperature" not in calls[-1] and "temperature" not in c.optional
    c.complete("s", "u")
    assert len(calls) == 3, "the dropped parameter stays dropped"


def test_other_400s_are_not_swallowed(monkeypatch):
    monkeypatch.setattr(llm, "_post", lambda *a: (_ for _ in ()).throw(LLMError("HTTP 400", 400, "bad model")))
    with pytest.raises(LLMError):
        OpenAICompatibleClient(model="m").complete("s", "u")


def test_reasoning_ran_out_of_tokens(monkeypatch):
    reply = {"choices": [{"message": {"content": None}, "finish_reason": "length"}]}
    monkeypatch.setattr(llm, "_post", lambda *a: reply)
    with pytest.raises(LLMError, match="max_tokens"):
        OpenAICompatibleClient(model="m").complete("s", "u")


def test_content_parts(monkeypatch):
    parts = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
    monkeypatch.setattr(llm, "_post", lambda *a: {"choices": [{"message": {"content": parts}}]})
    assert OpenAICompatibleClient(model="m").complete("s", "u") == "ab"
