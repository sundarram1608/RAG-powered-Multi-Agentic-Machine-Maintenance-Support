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
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agents"))                      # api, graph, config
sys.path.insert(0, str(ROOT))                                 # observability
sys.path.insert(0, str(ROOT / "synthetic_data" / "tables"))   # db_connection

import api  # agents/api.py

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
