"""
llms.py — LLM provider factory for the agent layer.

  get_reasoner(structured=, tools=) -> Groq Llama 3.3 70B   (reasoning / tool-calling:
                    supervisor, intake, diagnosis, deciders, actions, analytics, output)
  get_judge_structured(schema)      -> Gemini 2.5 Flash-Lite, bound to `schema`
                    (independent verifier), with a cross-family Qwen-on-Groq fallback.

Keys come from .env (loaded by config). Each factory optionally fails over to a
SECONDARY key of the same provider (GROQ_API_KEY_2 / GOOGLE_API_KEY_2) when the
primary returns a rate-limit / quota / capacity error — so a turn can still finish.
Failover is quota/capacity-only (config.is_rate_limit_error); request/validation
bugs are NOT retried on the backup key, they surface immediately.

Because `.with_structured_output()` / `.bind_tools()` don't exist on a fallback
wrapper, the binding is applied to EACH key's model first and the configured
runnables are then combined — hence the `structured=` / `tools=` params here rather
than callers binding the returned object. Switching providers stays a change in
this one file; every node calls these factories, not a provider directly.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # agents/ on path
import config

from langchain_core.runnables import Runnable
from langchain_groq import ChatGroq
from langchain_google_genai import ChatGoogleGenerativeAI


class _QuotaFailover(Runnable):
    """Try each runnable in order; advance to the next ONLY on a TRANSIENT infra error
    (config.is_transient_error — rate-limit / quota / capacity, or a connection /
    timeout blip). Any other error (e.g. a request/validation bug) is re-raised at
    once. If every candidate fails transiently, the last error propagates (so api.py
    can show the friendly message)."""

    def __init__(self, candidates):
        self._candidates = candidates

    def _run(self, invoke_one):
        last = None
        for i, cand in enumerate(self._candidates):
            try:
                return invoke_one(cand)
            except Exception as e:           # noqa: BLE001 — classify, then re-raise or fail over
                is_last = i == len(self._candidates) - 1
                if is_last or not config.is_transient_error(e):
                    raise
                last = e
        raise last                            # unreachable (loop returns or raises)

    def invoke(self, input, config=None, **kwargs):
        return self._run(lambda c: c.invoke(input, config, **kwargs))

    async def ainvoke(self, input, config=None, **kwargs):
        last = None
        for i, cand in enumerate(self._candidates):
            try:
                return await cand.ainvoke(input, config, **kwargs)
            except Exception as e:
                is_last = i == len(self._candidates) - 1
                if is_last or not config.is_transient_error(e):
                    raise
                last = e
        raise last


def _chain(candidates):
    """Bare runnable when there's only one; quota-failover wrapper when there's a backup."""
    return candidates[0] if len(candidates) == 1 else _QuotaFailover(candidates)


def _groq(model, api_key):
    return ChatGroq(model=model, temperature=config.REASONING_TEMPERATURE,
                    max_retries=config.LLM_MAX_RETRIES, api_key=api_key)


def _gemini(model, api_key):
    # Gemini is only ever the judge; fail fast (JUDGE_MAX_RETRIES) so a Gemini outage
    # falls over to the Qwen-on-Groq candidate quickly instead of hanging on retries.
    return ChatGoogleGenerativeAI(model=model, temperature=config.JUDGE_TEMPERATURE,
                                  max_retries=config.JUDGE_MAX_RETRIES, api_key=api_key)


def _configure(model, structured, tools):
    """Apply the caller's binding to a base model BEFORE it joins a failover chain."""
    if structured is not None:
        return model.with_structured_output(structured)
    if tools is not None:
        return model.bind_tools(tools)
    return model


def get_reasoner(structured=None, tools=None):
    """Groq Llama 3.3 70B — the primary reasoning / tool-calling model.

    Pass `structured=<schema>` for structured output or `tools=<tools>` to bind
    tools (the binding is applied per key so it survives failover). With no args it
    returns a plain chat model. Fails over to GROQ_API_KEY_2 if that key is set.
    """
    if not config.GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY is not set in .env — required for the reasoning model. "
            "Get a free key at https://console.groq.com → API Keys."
        )
    keys = [k for k in (config.GROQ_API_KEY, config.GROQ_API_KEY_2) if k]
    candidates = [_configure(_groq(config.REASONING_MODEL, k), structured, tools) for k in keys]
    return _chain(candidates)


def get_judge():
    """Gemini 2.5 Flash-Lite — the independent verifier (a different model family).

    Plain chat model, failing over to GOOGLE_API_KEY_2 if set. (Most callers want
    get_judge_structured, which also adds the cross-family Groq fallback.)
    """
    if not config.GOOGLE_API_KEY:
        raise RuntimeError(
            "GOOGLE_API_KEY is not set in .env — required for the judge model. "
            "Get a free key at https://aistudio.google.com → Get API key."
        )
    keys = [k for k in (config.GOOGLE_API_KEY, config.GOOGLE_API_KEY_2) if k]
    return _chain([_gemini(config.JUDGE_MODEL, k) for k in keys])


def get_judge_structured(schema):
    """Independent judge bound to `schema`, with key + cross-family failover.

    Tried in order: Gemini (key1, then key2) — the PRIMARY independent, non-Groq
    family — then Qwen-3 on Groq (key1, then key2) when Gemini is rate-limited or
    unavailable. Qwen is also a different family than the Llama reasoner, so the
    verifier's independence property holds. Returns a Runnable yielding `schema`.
    """
    if not config.GOOGLE_API_KEY:
        raise RuntimeError(
            "GOOGLE_API_KEY is not set in .env — required for the judge model. "
            "Get a free key at https://aistudio.google.com → Get API key."
        )
    candidates = [_gemini(config.JUDGE_MODEL, k).with_structured_output(schema)
                  for k in (config.GOOGLE_API_KEY, config.GOOGLE_API_KEY_2) if k]
    candidates += [_groq(config.JUDGE_FALLBACK_MODEL, k).with_structured_output(schema)
                   for k in (config.GROQ_API_KEY, config.GROQ_API_KEY_2) if k]
    return _chain(candidates)
