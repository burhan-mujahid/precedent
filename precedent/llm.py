"""Model clients. Standard library only (urllib), so installing Precedent never fights your stack.

A client does exactly one thing: send a system + user prompt, get text back, with
timeouts, retries with backoff on 429/5xx, and a clear error otherwise. All prompt
logic lives in reasoner.py.
"""

from __future__ import annotations

import http.client
import json
import os
import random
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol

MAX_RESPONSE_BYTES = 10_000_000   # a misbehaving endpoint cannot exhaust memory


class LLMError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status, self.body = status, body


class LLMParseError(LLMError):
    """The model answered, but not with usable JSON. Worth one repair attempt; other errors are not."""


class LLMClient(Protocol):
    name: str

    def complete(self, system: str, user: str, max_tokens: int | None = None) -> str: ...


# OpenAI-compatible providers that need no code, only a preset. Model names change too often to
# hard-code, so `model` is always required: `precedent doctor` lists what your key can use.
PRESETS: dict[str, dict[str, Any]] = {
    "openai": {"base_url": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY"},
    # Gemini 3 models always reason, and reasoning tokens count against max_tokens.
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "api_key_env": "GEMINI_API_KEY",
               "max_tokens": 8000, "reasoning_effort": "low"},
    # Moonshot Open Platform key (platform.moonshot.ai), not a Kimi Coding subscription key.
    "kimi": {"base_url": "https://api.moonshot.ai/v1", "api_key_env": "MOONSHOT_API_KEY", "max_tokens": 4000},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "api_key_env": "GROQ_API_KEY"},
    "ollama": {"base_url": "http://localhost:11434/v1", "api_key_env": None},
}
OPTIONAL_PARAMS = ("temperature", "reasoning_effort")
_TOKEN_PARAMS = ("max_tokens", "max_completion_tokens")


_CERT_HINT = ("TLS certificate check failed: this Python has no CA certificates. On macOS run "
              "'Install Certificates.command' from your Python folder, or `pip install certifi` "
              "(precedent uses it automatically), or set SSL_CERT_FILE to a CA bundle.")
_ssl_context: ssl.SSLContext | None = None


def _context() -> ssl.SSLContext:
    """Default TLS context; if the system trust store is empty (python.org macOS builds before
    'Install Certificates'), fall back to certifi's bundle when it is installed."""
    global _ssl_context
    if _ssl_context is None:
        ctx = ssl.create_default_context()
        if not ctx.cert_store_stats().get("x509_ca"):
            try:
                import certifi  # optional: pip install "precedent-ai[certs]"
                ctx = ssl.create_default_context(cafile=certifi.where())
            except ImportError:
                pass
        _ssl_context = ctx
    return _ssl_context


def _request(url: str, headers: dict[str, str], body: dict[str, Any] | None, timeout: float,
             retries: int) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    last: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                     headers={"content-type": "application/json", **headers})
        try:
            context = _context() if url.startswith("https") else None
            with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
                raw_bytes = resp.read(MAX_RESPONSE_BYTES + 1)
                if len(raw_bytes) > MAX_RESPONSE_BYTES:
                    raise LLMError(f"{url} returned more than {MAX_RESPONSE_BYTES} bytes; refusing to read it")
                raw = raw_bytes.decode("utf-8", "replace")
            try:
                out = json.loads(raw)
            except json.JSONDecodeError:
                raise LLMError(f"{url} returned non-JSON (check base_url): {raw[:200]!r}") from None
            if not isinstance(out, dict):
                raise LLMError(f"{url} returned unexpected JSON: {raw[:200]!r}")
            return out
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:800]
            if exc.code in (408, 409, 429) or exc.code >= 500:
                last = LLMError(f"HTTP {exc.code}: {detail}", exc.code, detail)
                retry_after = exc.headers.get("retry-after") if exc.headers else None
                delay = float(retry_after) if retry_after and retry_after.replace(".", "").isdigit() else 2 ** attempt
                time.sleep(min(delay, 30) + random.random())
                continue
            hint = " (check the API key and that it belongs to this provider)" if exc.code in (401, 403) else ""
            raise LLMError(f"HTTP {exc.code}{hint}: {detail}", exc.code, detail) from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as exc:
            if isinstance(getattr(exc, "reason", exc), ssl.SSLCertVerificationError):
                raise LLMError(_CERT_HINT) from exc   # retrying cannot fix a missing trust store
            last = exc
            time.sleep(min(2 ** attempt, 30) + random.random())
    raise LLMError(f"request to {url} failed after {retries + 1} attempts: {last}")


def _post(url: str, headers: dict[str, str], body: dict[str, Any], timeout: float, retries: int) -> dict[str, Any]:
    return _request(url, headers, body, timeout, retries)


class Usage:
    """Running totals for one client (thread-safe): calls and tokens as reported by the provider."""

    def __init__(self) -> None:
        self.calls = self.input_tokens = self.output_tokens = 0
        self._lock = threading.Lock()

    def add(self, input_tokens: Any, output_tokens: Any) -> None:
        with self._lock:
            self.calls += 1
            self.input_tokens += int(input_tokens or 0)
            self.output_tokens += int(output_tokens or 0)

    def __str__(self) -> str:
        return f"{self.calls} model call(s), {self.input_tokens} input / {self.output_tokens} output tokens"


class AnthropicClient:
    name = "anthropic"

    def __init__(self, model: str | None = None, api_key: str | None = None, timeout_s: float = 60,
                 max_retries: int = 3, base_url: str | None = None, max_tokens: int | None = None, **_: Any):
        self.model = model or os.environ.get("PRECEDENT_MODEL")
        if not self.model:
            raise LLMError("set provider.model in precedent.yaml (e.g. a current Claude Sonnet model id)")
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        self.api_key: str = key
        self.base = (base_url or "https://api.anthropic.com").rstrip("/")
        self.timeout, self.retries = timeout_s, max_retries
        self.max_tokens = int(max_tokens or 2000)
        self.usage = Usage()

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}

    def complete(self, system: str, user: str, max_tokens: int | None = None) -> str:
        out = _post(self.base + "/v1/messages", self._headers(),
                    {"model": self.model, "max_tokens": max_tokens or self.max_tokens, "system": system,
                     "messages": [{"role": "user", "content": user}]}, self.timeout, self.retries)
        u = out.get("usage") or {}
        self.usage.add(u.get("input_tokens"), u.get("output_tokens"))
        text = "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")
        if not text and out.get("stop_reason") == "max_tokens":
            raise LLMError("model hit max_tokens before answering; raise provider.max_tokens")
        return text

    def list_models(self) -> list[str]:
        out = _request(self.base + "/v1/models", self._headers(), None, self.timeout, 0)
        return [m["id"] for m in out.get("data", [])]


class OpenAICompatibleClient:
    """OpenAI, Gemini, Kimi (Moonshot), OpenRouter, Groq, Ollama, vLLM, LM Studio..."""

    def __init__(self, model: str | None = None, api_key: str | None = None, timeout_s: float = 60,
                 max_retries: int = 3, base_url: str | None = None, max_tokens: int | None = None,
                 temperature: float | None = 0, reasoning_effort: str | None = None, name: str = "openai", **_: Any):
        self.name = name
        self.model = model or os.environ.get("PRECEDENT_MODEL")
        if not self.model:
            raise LLMError(f"set provider.model in precedent.yaml for '{name}' "
                           "(run `precedent doctor` to list the models your key can use)")
        self.api_key = api_key or ""
        self.base = (base_url or os.environ.get("OPENAI_BASE_URL") or PRESETS["openai"]["base_url"]).rstrip("/")
        self.timeout, self.retries = timeout_s, max_retries
        self.max_tokens = int(max_tokens or 2000)
        self.usage = Usage()
        self.token_param = "max_tokens"   # newer OpenAI models want max_completion_tokens; switched on a 400
        # Optional parameters a provider/model may reject; dropped automatically on a 400 that names them.
        self.optional: dict[str, Any] = {k: v for k, v in
                                         (("temperature", temperature), ("reasoning_effort", reasoning_effort))
                                         if v is not None}
        self._lock = threading.Lock()

    def _headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def complete(self, system: str, user: str, max_tokens: int | None = None) -> str:
        # Work on a per-call copy: mining calls this from several threads, and one thread learning
        # that a parameter is rejected must not change the list another thread is checking.
        with self._lock:
            optional, token_param = dict(self.optional), self.token_param
        while True:
            body = {"model": self.model, token_param: max_tokens or self.max_tokens, **optional,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            try:
                out = _post(self.base + "/chat/completions", self._headers(), body, self.timeout, self.retries)
                break
            except LLMError as exc:
                err = exc.body.lower() if exc.status == 400 else ""
                if token_param == "max_tokens" and "max_completion_tokens" in err:
                    token_param = "max_completion_tokens"
                    with self._lock:
                        self.token_param = token_param
                    continue
                rejected = [k for k in optional if k in err]
                if not rejected:
                    raise
                with self._lock:
                    for k in rejected:      # e.g. a model that only accepts its default temperature
                        optional.pop(k)
                        self.optional.pop(k, None)
        u = out.get("usage") or {}
        self.usage.add(u.get("prompt_tokens"), u.get("completion_tokens"))   # completion includes reasoning
        choice = (out.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        if isinstance(content, list):   # some providers return content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if not content and choice.get("finish_reason") == "length":
            raise LLMError("model hit max_tokens before answering (reasoning models spend tokens thinking first); "
                           "raise provider.max_tokens or set provider.reasoning_effort: low")
        return content

    def list_models(self) -> list[str]:
        out = _request(self.base + "/models", self._headers(), None, self.timeout, 0)
        return sorted(str(m.get("id", "")).removeprefix("models/") for m in out.get("data", []))


def parse_json(text: str) -> Any:
    """Pull the first JSON object/array out of a model reply (tolerates ```json fences and chatter)."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start = text.find(open_c)
        while start != -1:
            depth, in_str, esc = 0, False, False
            for i in range(start, len(text)):
                c = text[i]
                if in_str:
                    esc = (c == "\\") and not esc
                    if c == '"' and not esc:
                        in_str = False
                    continue
                if c == '"':
                    in_str = True
                elif c == open_c:
                    depth += 1
                elif c == close_c:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except json.JSONDecodeError:
                            break
            start = text.find(open_c, start + 1)
    raise LLMParseError(f"model did not return JSON: {text[:200]!r}")


def resolve_provider(cfg: dict[str, Any]) -> dict[str, Any]:
    """Merge a provider preset with explicit settings from precedent.yaml (explicit wins)."""
    kind = (cfg.get("kind") or "offline").lower()
    preset = PRESETS.get("openai" if kind == "openai-compatible" else kind, {})
    merged = {**preset, **{k: v for k, v in cfg.items() if v is not None}}
    merged["kind"] = kind
    return merged


def _check_transport(base_url: str) -> None:
    """An API key only travels over HTTPS, except to this machine (Ollama, vLLM, LM Studio)."""
    u = urllib.parse.urlparse(base_url)
    if u.scheme == "https":
        return
    if u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1", "::1"):
        return
    raise LLMError(f"refusing to send an API key to {base_url!r}: use https (plain http is only allowed to localhost)")


def make_client(cfg: dict[str, Any]) -> LLMClient | None:
    p = resolve_provider(cfg)
    kind = p["kind"]
    if kind == "offline":
        return None
    env = p.get("api_key_env") or ("ANTHROPIC_API_KEY" if kind == "anthropic" else None)
    key = os.environ.get(env) if env else None
    if env and not key and kind != "ollama":
        raise LLMError(f"{env} is not set (export {env}=... before running)")
    base = p.get("base_url") or ""
    if key and base:
        _check_transport(base)
    common = dict(model=p.get("model"), api_key=key, timeout_s=p.get("timeout_s", 60),
                  max_retries=p.get("max_retries", 3), base_url=p.get("base_url"), max_tokens=p.get("max_tokens"))
    if kind == "anthropic":
        return AnthropicClient(**common)
    if kind in PRESETS or kind == "openai-compatible":
        return OpenAICompatibleClient(**common, temperature=p.get("temperature", 0),
                                      reasoning_effort=p.get("reasoning_effort"), name=kind)
    raise LLMError(f"unknown provider kind {kind!r} (offline | anthropic | {' | '.join(PRESETS)})")
