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


# ── streaming variants (Phase 6b) — a live activity feed, then the final result ──
# Streamed with THREE modes at once (astream yields (mode, chunk) tuples):
#   "updates"  -> one {node: state-delta} per finished node; we accumulate the delta
#                 (so _interpret still works) and emit a "decision" line summarising it.
#   "messages" -> LLM token chunks; we forward tokens from the OUTPUT node as "token"
#                 events (the answer types out live) and ignore other nodes (JSON).
#   "custom"   -> tool/sub-step events a node emitted via streaming.emit() -> "tool".
# Event shapes yielded: {"type":"decision"|"tool"|"token"|"result", ...}.

def _summarize(node: str, d: dict) -> str | None:
    """A short, human 'decision' line for a finished node (None -> nothing to show)."""
    if node == "supervisor" and d.get("intent"):
        return f"🧭 Routing → {d['intent']}"
    if node == "advice" and d.get("advice_route"):
        return {"answer": "💡 Preparing advice", "ask": "❓ Checking: facing it now, or asking?",
                "troubleshoot": "🔧 Handing off to troubleshooting"}.get(d["advice_route"])
    if node == "intake":
        if d.get("needs_clarification"):
            return "🛠 Intake → need a bit more info"
        if d.get("machine_id"):
            return f"🛠 Intake → machine {d['machine_id']}, symptom captured"
    if node == "diagnosis" and d.get("diagnosis"):
        dx = d["diagnosis"]
        return f"🔬 Diagnosis → {str(dx.get('root_cause'))[:60]} (confidence {dx.get('retrieval_confidence')})"
    if node == "verifier" and d.get("verdict"):
        v = d["verdict"]
        return f"⚖️ Verifier → {'approved' if v.get('approved') else 'needs rework'} ({v.get('score')}/5)"
    if node == "analytics_generate" and d.get("sql_plan"):
        return "🧮 Wrote a SQL query"
    if node == "text_to_sql_reviewer" and d.get("sql_review") is not None:
        return f"🔎 SQL review → {'approved' if d['sql_review'].get('approved') else 'rejected, retrying'}"
    if node == "decider" and d.get("decision_path"):
        return f"🧑‍🔧 Chosen path → {d['decision_path']}"
    if node == "manage_resolve":
        mp = d.get("manage_plan") or {}
        if d.get("needs_clarification"):
            return "📇 Looking up the incident…"
        if mp.get("plan_summary"):
            return f"📝 {mp['plan_summary'][:70]}"
    return None


async def _astream(graph_input, thread_id, user_id, meta_value, run_name, turn_id):
    turn_id = turn_id or obs.new_turn_id()
    cfg, run_id, meta = obs.make_config(
        thread_id, user_id, meta_value, turn_id=turn_id, run_name=run_name)
    result = {}
    answer_acc = ""      # accumulated answer tokens (to drop a redundant final full-message emit)
    try:
        async for mode, chunk in app_graph.astream(
                graph_input, cfg, stream_mode=["updates", "messages", "custom"]):
            if mode == "custom":                                   # node-emitted tool/step line
                if isinstance(chunk, dict) and chunk.get("text"):
                    yield {"type": chunk.get("type", "tool"), "text": chunk["text"]}
            elif mode == "messages":                               # LLM tokens (answer only)
                msg, md = chunk
                if (md or {}).get("langgraph_node") == "output":
                    text = getattr(msg, "content", "") or ""
                    # forward streaming deltas; skip a trailing full-message repeat (== acc)
                    if text and text != answer_acc:
                        yield {"type": "token", "text": text}
                        answer_acc += text
            elif mode == "updates":                                # a node finished
                for node, delta in chunk.items():
                    if node == "__interrupt__":
                        result["__interrupt__"] = delta
                    elif isinstance(delta, dict):
                        result.update(delta)
                        line = _summarize(node, delta)
                        if line:
                            yield {"type": "decision", "text": line}
    except Exception as e:
        yield {"type": "result", **_error_result(e, turn_id, run_id)}
        return
    obs.enrich_run(run_id, meta, result)
    yield {"type": "result", **_interpret(result, turn_id, run_id)}


async def stream_turn(thread_id: str, user_id: str, message: str, turn_id: str = None):
    async for ev in _astream(
            {"user_input": message, "current_user_id": user_id,
             "messages": [HumanMessage(content=message)]},
            thread_id, user_id, message, "turn:start", turn_id):
        yield ev


async def stream_resume(thread_id: str, value, turn_id: str = None, user_id: str = None):
    async for ev in _astream(
            Command(resume=value), thread_id, user_id, str(value), "turn:resume", turn_id):
        yield ev
