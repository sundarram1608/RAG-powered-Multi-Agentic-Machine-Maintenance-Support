"""
tracing.py — LangSmith observability for the agent workflow (Phase 5a).

Backstage instrumentation. Every turn through the graph is captured as a LangSmith
trace — a "run tree": the root run = one invoke, child runs = each node, leaf runs =
each LLM / MCP-tool call. We do NOT create spans by hand; LangGraph's tracer builds
the tree automatically once a tracer is attached. This module:
  - attaches metadata so traces are organised + filterable (thread / turn / user / models),
  - masks PII (phone / email) BEFORE anything is uploaded to LangSmith,
  - annotates the root run with the turn's OUTCOME after it finishes.

Three ids organise the data (see observability/README.md):
  thread_id   -> one conversation        (groups many turns in LangSmith's Threads view)
  turn_id     -> one request + its resumes (distinguishes a turn from the next within a thread)
  run (trace) -> one invoke              (a start_turn, or a single resume_turn)

Tracing is gated by LANGSMITH_TRACING in .env. We attach our OWN masking tracer (so
PII never reaches LangSmith) and silence the env auto-tracer so runs aren't uploaded
twice (once unmasked). Importing this module therefore disables env-based tracing
process-wide — every traced turn must go through make_config().
"""

import os
import re
import uuid
from pathlib import Path

from dotenv import load_dotenv

# Load .env so LANGSMITH_* is available regardless of import order (observability
# may be imported before agents/config.py runs its own load_dotenv).
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

_TRACING = os.getenv("LANGSMITH_TRACING", "").lower() == "true"
# We attach an explicit, PII-masking tracer in make_config(); silence the env
# auto-tracer so the same run isn't uploaded twice (the second copy unmasked).
os.environ["LANGSMITH_TRACING"] = "false"
os.environ.pop("LANGCHAIN_TRACING_V2", None)

PROJECT = os.getenv("LANGSMITH_PROJECT", "fdm-agentic")

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"\b\d{7,}\b")


def tracing_on() -> bool:
    return _TRACING


def _mask_text(s: str) -> str:
    s = _EMAIL_RE.sub("[redacted-email]", s)
    return _PHONE_RE.sub("[redacted-phone]", s)


def _redact(obj):
    """Recursively mask phone / email in any inputs/outputs before upload."""
    if isinstance(obj, str):
        return _mask_text(obj)
    if isinstance(obj, dict):
        return {k: _redact(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return type(obj)(_redact(v) for v in obj)
    return obj


# One masking client + tracer per process (created lazily so import is cheap).
_client = None
_tracer = None


def _get_tracer():
    global _client, _tracer
    if _tracer is None:
        from langsmith import Client
        from langchain_core.tracers.langchain import LangChainTracer
        _client = Client(hide_inputs=_redact, hide_outputs=_redact)
        _tracer = LangChainTracer(project_name=PROJECT, client=_client)
    return _tracer


def get_client():
    """The PII-masking LangSmith client (lazily built)."""
    _get_tracer()
    return _client


def new_turn_id() -> str:
    """A fresh turn id (one request + any clarification resumes)."""
    return uuid.uuid4().hex


def _base_metadata(thread_id, user_id, turn_id) -> dict:
    models = {}
    try:
        import config  # agents/ is on sys.path when called from api.py
        models = {
            "reasoning": config.REASONING_MODEL,
            "judge": config.JUDGE_MODEL,
            "judge_fallback": config.JUDGE_FALLBACK_MODEL,
        }
    except Exception:
        pass
    return {
        "session_id": thread_id,   # LangSmith Threads view groups on session_id
        "thread_id": thread_id,
        "turn_id": turn_id,
        "user_id": user_id,
        "models": models,
    }


def make_config(thread_id, user_id, message, *, turn_id, run_name, base=None):
    """Build the runnable config for ONE invoke.

    Always sets the checkpointer thread + recursion_limit. When tracing is on, also
    attaches the masking tracer, metadata, tags, run_name, and a known run_id we can
    enrich after the turn. Returns (config, run_id, base_metadata).
    """
    cfg = dict(base or {})
    cfg.setdefault("configurable", {})
    cfg["configurable"]["thread_id"] = thread_id
    cfg.setdefault("recursion_limit", 50)

    run_id = uuid.uuid4()
    metadata = _base_metadata(thread_id, user_id, turn_id)

    if _TRACING:
        cfg["run_id"] = run_id
        cfg["run_name"] = run_name
        cfg["metadata"] = metadata
        cfg["tags"] = ["fdm-agentic"]
        cfg.setdefault("callbacks", []).append(_get_tracer())
    return cfg, run_id, metadata


def _outcome(state) -> dict:
    dx = state.get("diagnosis") or {}
    vr = state.get("verdict") or {}
    return {
        "intent": state.get("intent"),
        "machine_id": state.get("machine_id"),
        "decision_path": state.get("decision_path"),
        "needs_technician": dx.get("needs_technician"),
        "verdict_score": vr.get("score"),
        "verdict_approved": vr.get("approved"),
        "verifier_exhausted": state.get("verifier_exhausted"),
        "action": (state.get("action_result") or {}).get("action"),
        "prompt_versions": state.get("prompt_versions"),
    }


def enrich_run(run_id, base_metadata, state) -> None:
    """Annotate the (auto-created) root run with the turn's OUTCOME, for filtering.
    Merges the creation metadata so those fields aren't lost. Never raises."""
    if not _TRACING:
        return
    try:
        merged = {**(base_metadata or {}), **_outcome(state)}
        get_client().update_run(run_id, extra={"metadata": merged})
    except Exception:
        pass  # observability must never break a turn
