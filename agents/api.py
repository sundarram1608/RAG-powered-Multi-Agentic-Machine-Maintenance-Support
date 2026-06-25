"""
api.py — the thin boundary the app (CLI now, Streamlit in Phase 6) calls.

Two functions, keyed by thread_id (one conversation = one thread; the MemorySaver
checkpointer isolates state per thread):

  start_turn(thread_id, user_id, message) -> a NEW request (enters at `input`,
      which resets per-request scratch). Mints a fresh turn_id.
  resume_turn(thread_id, value, *, turn_id, user_id) -> supply the value an
      interrupt asked for (continues mid-graph). Pass the turn_id from the paused
      start so all of one request's traces share it.

Both return a dict the caller switches on:
  {"kind": "answer", "content": <final_response>, "turn_id": ...}             # turn done
  {"kind": "clarify"|"decision"|"choice"|"approve", "payload": …, "turn_id": …}  # paused
  {"kind": "error", "content": <friendly message>, "turn_id": ...}            # provider/other failure

Provider failures (rate limits, outages) are caught here and returned as a friendly
"error" message instead of propagating as a raw exception to the UI — the traceback
still goes to stderr + the LangSmith trace for debugging.

Observability (Phase 5a): every invoke is traced to LangSmith via observability.*,
grouped by thread_id (session) and turn_id, with PII masked and outcome metadata
attached after the run. Tracing is a no-op unless LANGSMITH_TRACING=true in .env.
"""

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))           # agents/ on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))       # repo root -> observability
from graph import app_graph

import config
import observability as obs
from langgraph.types import Command
from langchain_core.messages import HumanMessage


def _friendly_error(exc: Exception) -> str:
    # Only reached after llms.py has already tried every configured backup key.
    if config.is_rate_limit_error(exc):
        return ("⚠️ I've hit the free-tier usage limit for the AI service right now, so I "
                "can't finish that. The tier resets at midnight every day. Please wait "
                "until reset and then try again")
    return "⚠️ Sorry — something went wrong on my side. Please try again in a moment."


def _interpret(result: dict, turn_id: str, run_id) -> dict:
    # run_id lets the UI attach feedback later via observability.log_feedback(run_id, score)
    rid = str(run_id) if run_id else None
    interrupts = result.get("__interrupt__")
    if interrupts:
        payload = interrupts[0].value
        return {"kind": payload.get("type", "needs_input"), "payload": payload,
                "turn_id": turn_id, "run_id": rid}
    return {"kind": "answer", "content": result.get("final_response"),
            "turn_id": turn_id, "run_id": rid}


def _error_result(exc, turn_id, run_id) -> dict:
    traceback.print_exc(file=sys.stderr)   # full traceback to logs (+ it's in the trace)
    return {"kind": "error", "content": _friendly_error(exc),
            "turn_id": turn_id, "run_id": str(run_id) if run_id else None}


async def start_turn(thread_id: str, user_id: str, message: str, turn_id: str = None) -> dict:
    turn_id = turn_id or obs.new_turn_id()
    cfg, run_id, meta = obs.make_config(
        thread_id, user_id, message, turn_id=turn_id, run_name="turn:start")
    try:
        result = await app_graph.ainvoke(
            {"user_input": message, "current_user_id": user_id,
             "messages": [HumanMessage(content=message)]}, cfg)
    except Exception as e:
        return _error_result(e, turn_id, run_id)
    obs.enrich_run(run_id, meta, result)
    return _interpret(result, turn_id, run_id)


async def resume_turn(thread_id: str, value, turn_id: str = None, user_id: str = None) -> dict:
    turn_id = turn_id or obs.new_turn_id()
    cfg, run_id, meta = obs.make_config(
        thread_id, user_id, str(value), turn_id=turn_id, run_name="turn:resume")
    try:
        result = await app_graph.ainvoke(Command(resume=value), cfg)
    except Exception as e:
        return _error_result(e, turn_id, run_id)
    obs.enrich_run(run_id, meta, result)
    return _interpret(result, turn_id, run_id)
