"""
api.py — the thin boundary the app (CLI now, Streamlit in Phase 6) calls.

Two functions, keyed by thread_id (one conversation = one thread; the MemorySaver
checkpointer isolates state per thread):

  start_turn(thread_id, user_id, message) -> a NEW request (enters at `input`,
      which resets per-request scratch).
  resume_turn(thread_id, value)           -> supply the value an interrupt asked
      for (continues mid-graph; does NOT re-run upstream nodes).

Both return a dict the caller switches on:
  {"kind": "answer", "content": <final_response>}                  # turn done
  {"kind": "clarify"|"decision"|"choice"|"approve", "payload": …}  # paused for the user
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # agents/ on path
from graph import app_graph

from langgraph.types import Command


def _cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 50}


def _interpret(result: dict) -> dict:
    interrupts = result.get("__interrupt__")
    if interrupts:
        payload = interrupts[0].value
        return {"kind": payload.get("type", "needs_input"), "payload": payload}
    return {"kind": "answer", "content": result.get("final_response")}


async def start_turn(thread_id: str, user_id: str, message: str) -> dict:
    result = await app_graph.ainvoke(
        {"user_input": message, "current_user_id": user_id}, _cfg(thread_id))
    return _interpret(result)


async def resume_turn(thread_id: str, value) -> dict:
    result = await app_graph.ainvoke(Command(resume=value), _cfg(thread_id))
    return _interpret(result)
