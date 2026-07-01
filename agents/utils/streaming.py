"""
streaming.py — helper for nodes to surface live activity (tool calls, sub-steps) to the
UI during `astream(stream_mode="custom")`.

`emit(text, kind="tool")` pushes one event onto the graph's custom stream when the run
is being streamed, and is a NO-OP otherwise (normal ainvoke, tests, CLI) — so nodes can
call it unconditionally. The UI (app/app_utils.py) renders these as lines in the live
activity log.
"""


def emit(text: str, kind: str = "tool") -> None:
    """Emit one activity event to the custom stream, if we're inside a streamed run."""
    try:
        from langgraph.config import get_stream_writer
        get_stream_writer()({"type": kind, "text": text})
    except Exception:
        pass  # not in a streaming context (plain ainvoke / tests) -> ignore


# Friendly labels so a tool call reads as an action, not a function name.
_TOOL_LABELS = {
    "get_machine": "Looking up machine",
    "user_manual_retrieval": "Searching the manual",
    "safety_retrieval": "Checking the safety guide",
    "get_incident": "Fetching the incident",
    "list_incidents": "Listing incidents",
    "get_incident_history": "Reviewing past incidents",
    "get_overdue_status": "Checking overdue status",
    "get_inventory_status": "Checking spare-part inventory",
    "list_available_technicians": "Checking technician availability",
    "find_available_technician": "Finding an available technician",
    "book_technician_slot": "Booking the technician",
    "update_incident": "Updating the incident",
    "create_incident": "Logging the incident",
    "send_email": "Sending a notification",
    "run_readonly_query": "Querying the database",
}


def emit_tool(name: str, args: dict | None = None) -> None:
    """Emit a friendly 'calling tool X' line (a no-op outside a streamed run)."""
    label = _TOOL_LABELS.get(name, name)
    detail = ""
    for key in ("machine_id", "incident_id", "employee_id", "mvc_code", "query", "status"):
        if args and args.get(key):
            detail = f" · {str(args[key])[:40]}"
            break
    emit(f"🔧 {label}{detail}", kind="tool")
