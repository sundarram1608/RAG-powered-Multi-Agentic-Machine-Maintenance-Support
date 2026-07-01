"""
backend.py — the bridge between the (synchronous) Streamlit UI and the (async) agent
API. A single asyncio event loop runs on a daemon thread for the app's lifetime, so the
MCP client + LangGraph stay bound to one loop across Streamlit reruns; each UI call
submits a coroutine to that loop and blocks for the result.

Reuses agents/api.py (start_turn / resume_turn) unchanged. The LangGraph app_graph +
its MemorySaver are module-level, so paused turns survive Streamlit reruns and resume
correctly.
"""

import asyncio
import queue
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agents"))                      # api, graph, config
sys.path.insert(0, str(ROOT))                                 # observability
sys.path.insert(0, str(ROOT / "synthetic_data" / "tables"))   # db_connection

import api  # agents/api.py
import observability as obs

_loop = None
_lock = threading.Lock()


def _loop_thread():
    global _loop
    with _lock:
        if _loop is None:
            _loop = asyncio.new_event_loop()
            threading.Thread(target=_loop.run_forever, daemon=True).start()
    return _loop


def _run(coro):
    """Submit a coroutine to the persistent loop and block for its result."""
    return asyncio.run_coroutine_threadsafe(coro, _loop_thread()).result()


def start_turn(thread_id, user_id, message) -> dict:
    return _run(api.start_turn(thread_id, user_id, message))


def resume_turn(thread_id, value, turn_id, user_id) -> dict:
    return _run(api.resume_turn(thread_id, value, turn_id=turn_id, user_id=user_id))


# ── streaming (Phase 6b): drive an async generator on the bg loop, hand events to
# the (sync) Streamlit thread through a thread-safe queue. Yields the same events
# api.stream_* yield: {"type":"progress","node":...} then {"type":"result", ...}. ──
_STREAM_DONE = object()


def _drain(agen):
    q = queue.Queue()

    async def _pump():
        try:
            async for item in agen:
                q.put(item)
        except Exception:                       # safety net — surface as a result
            q.put({"type": "result", "kind": "error",
                   "content": "⚠️ Sorry — something went wrong on my side. Please try again in a moment."})
        finally:
            q.put(_STREAM_DONE)

    asyncio.run_coroutine_threadsafe(_pump(), _loop_thread())
    while True:
        item = q.get()
        if item is _STREAM_DONE:
            return
        yield item


def stream_turn(thread_id, user_id, message):
    yield from _drain(api.stream_turn(thread_id, user_id, message))


def stream_resume(thread_id, value, turn_id, user_id):
    yield from _drain(api.stream_resume(thread_id, value, turn_id=turn_id, user_id=user_id))


def log_feedback(run_id, score) -> None:
    """Phase 6c: record a 👍/👎 on a turn's LangSmith run (score 1=up, 0=down). A 👎 is
    also flagged to the review queue so a human can curate it. Sync (a LangSmith call,
    not the graph); no-ops when tracing is off. Never raises into the UI."""
    try:
        obs.log_feedback(run_id, score)
        if score == 0:
            obs.flag_for_review(run_id, "user thumbs-down")
    except Exception:
        pass


def list_operators():
    """Active employees for the sidebar: list of (employee_id, 'E01 — Name (Role)')."""
    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT employee_id, full_name, role FROM employees "
                "WHERE status = 'Active' ORDER BY employee_id")
    rows = cur.fetchall()
    conn.close()
    return [(r["employee_id"], f"{r['employee_id']} — {r['full_name']} ({r['role']})") for r in rows]
