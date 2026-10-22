"""
providers.py — LLM provider adapters shared by Forgemaster and NÓTT.

Two providers, both selected by an ``AgentSpec`` from ``config.py``:

  anthropic          the Anthropic Messages API. With a ``base_url`` it targets
                     any Anthropic-compatible server (e.g. LM Studio).
  openai_compatible  POST {base_url}/chat/completions. Covers local servers that
                     speak the OpenAI wire format (llama.cpp, vLLM, Ollama,
                     LM Studio). Standard library only — no extra dependency.

Every call returns ``(text, input_tokens, output_tokens, latency_ms)`` and
raises ``RuntimeError`` on a missing key, an unreachable endpoint, an HTTP
error or a reply cut off at ``max_tokens``. Callers treat that as a dispatch
failure.

Keys are read from the environment at call time, so ``.env`` edits are picked
up without restarting the server.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from config import AgentSpec

_REPO_ROOT = Path(__file__).parent.parent

# Local models are slow on long outputs; override with NOVA_PROVIDER_TIMEOUT_S.
_DEFAULT_TIMEOUT_S = 300.0


def _load_env() -> None:
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=_REPO_ROOT / ".env", override=True)


def _api_key(api_key_env: str, provider_label: str) -> str:
    """Return the key held in ``api_key_env``; ``""`` when no key is configured."""
    if not api_key_env:
        return ""
    key = os.environ.get(api_key_env, "")
    if not key:
        raise RuntimeError(f"{api_key_env} is not set; cannot dispatch to {provider_label}.")
    return key


def call_anthropic(
    model: str,
    prompt: str,
    max_tokens: int = 4096,
    cached_system: str = "",
    base_url: str = "",
    api_key_env: str = "CLAUDE_API_KEY",
) -> tuple[str, int, int, int]:
    """
    Call an Anthropic (or Anthropic-compatible) model with a single user message.

    If cached_system is provided it is sent as the system prompt with
    cache_control so subsequent calls with the same string get cache reads
    instead of writes (pair with nova_cache_prewarm).
    """
    import anthropic

    _load_env()
    key = _api_key(api_key_env, "Anthropic") or "local"

    t0 = time.time()
    client_kwargs: dict = {"api_key": key}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = anthropic.Anthropic(**client_kwargs)

    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if cached_system:
        kwargs["system"] = [{
            "type": "text",
            "text": cached_system,
            "cache_control": {"type": "ephemeral"},
        }]

    response = client.messages.create(**kwargs)
    text = "".join(
        block.text for block in (response.content or [])
        if getattr(block, "type", "") == "text"
    )
    if getattr(response, "stop_reason", None) == "max_tokens":
        raise RuntimeError(f"{model} output truncated at max_tokens={max_tokens}")
    in_tok = getattr(response.usage, "input_tokens", 0)
    out_tok = getattr(response.usage, "output_tokens", 0)
    latency_ms = int((time.time() - t0) * 1000)
    return text, in_tok, out_tok, latency_ms


def call_openai_compatible(
    model: str,
    prompt: str,
    base_url: str,
    max_tokens: int = 4096,
    cached_system: str = "",
    api_key_env: str = "",
) -> tuple[str, int, int, int]:
    """
    Call an OpenAI-compatible ``/chat/completions`` endpoint.

    ``cached_system`` is sent as a plain system message — prompt caching is an
    Anthropic feature, so here it only supplies shared context. The
    Authorization header is sent only when ``api_key_env`` names a set variable;
    local servers usually need none.
    """
    _load_env()
    key = _api_key(api_key_env, base_url)
    url = base_url.rstrip("/") + "/chat/completions"

    messages: list[dict] = []
    if cached_system:
        messages.append({"role": "system", "content": cached_system})
    messages.append({"role": "user", "content": prompt})
    body = json.dumps({"model": model, "messages": messages, "max_tokens": max_tokens}).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    timeout = float(os.environ.get("NOVA_PROVIDER_TIMEOUT_S", _DEFAULT_TIMEOUT_S))

    t0 = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"{url} returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"cannot reach {url}: {exc.reason}") from exc

    try:
        choice = data["choices"][0]
        text = (choice.get("message") or {}).get("content") or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"{url} returned an unexpected response shape") from exc
    if choice.get("finish_reason") == "length":
        raise RuntimeError(f"{model} output truncated at max_tokens={max_tokens}")
    usage = data.get("usage") or {}
    in_tok = int(usage.get("prompt_tokens") or 0)
    out_tok = int(usage.get("completion_tokens") or 0)
    latency_ms = int((time.time() - t0) * 1000)
    return text, in_tok, out_tok, latency_ms


def call_agent(
    spec: AgentSpec,
    prompt: str,
    max_tokens: int = 4096,
    cached_system: str = "",
) -> tuple[str, int, int, int]:
    """Dispatch *prompt* to the agent described by *spec*."""
    if spec.provider == "anthropic":
        return call_anthropic(
            spec.model, prompt, max_tokens=max_tokens, cached_system=cached_system,
            base_url=spec.base_url, api_key_env=spec.api_key_env,
        )
    if spec.provider == "openai_compatible":
        return call_openai_compatible(
            spec.model, prompt, spec.base_url, max_tokens=max_tokens,
            cached_system=cached_system, api_key_env=spec.api_key_env,
        )
    raise ValueError(f"Unknown provider {spec.provider!r} for model {spec.model!r}")
