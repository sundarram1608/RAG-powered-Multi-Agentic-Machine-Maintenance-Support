"""
test_e2e.py — Phase 4c end-to-end journeys through the compiled graph.

Prereq: HTTP services server running (python mcp_server/server.py http).
Read-only paths (refusal/general/analytics) go through api.start_turn. Write paths
(troubleshoot) invoke app_graph directly with email_dry_run=True (no real emails)
and CLEAN UP every incident/booking they create. Needs GROQ + GOOGLE keys.
"""

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))           # agents/
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "synthetic_data" / "tables"))
from api import resume_turn, start_turn
from graph import app_graph
from langgraph.types import Command
from db_connection import get_connection


def _cfg(tid):
    return {"configurable": {"thread_id": tid}, "recursion_limit": 50}


async def _drive(tid, initial, resumes):
    """Invoke once, then feed `resumes` in order to each interrupt. Return final state."""
    res = await app_graph.ainvoke(initial, _cfg(tid))
    for value in resumes:
        intr = res.get("__interrupt__")
        if not intr:
            break
        print(f"    ↳ interrupt[{intr[0].value.get('type')}] -> resume {value!r}")
        res = await app_graph.ainvoke(Command(resume=value), _cfg(tid))
    return res


def _cleanup_incident(incident_id, emp=None, date=None, supervisor=False):
    c = get_connection(); cur = c.cursor()
    if emp and date:
        if supervisor:
            cur.execute("DELETE FROM technician_schedule WHERE `date`=%s AND employee_id=%s", (date, emp))
        else:
            cur.execute("UPDATE technician_schedule SET availability_status='Available' "
                        "WHERE `date`=%s AND employee_id=%s", (date, emp))
    cur.execute("DELETE FROM incidents WHERE incident_id=%s", (incident_id,))
    c.commit(); c.close()


async def main():
    print("=" * 70)
    # ---- read-only journeys via the api boundary ----
    for label, msg in [
        ("refusal",   "What's the capital of France?"),
        ("general",   "What can you help me with?"),
        ("analytics", "How many incidents are still open?"),
    ]:
        out = await start_turn(f"ro-{uuid.uuid4().hex[:6]}", "E01", msg)
        print(f"\n[{label}] kind={out['kind']}\n    {str(out.get('content'))[:300]}")

    # ---- troubleshoot -> technician (needs_technician path, no decider) ----
    print("\n" + "=" * 70)
    tid = f"tech-{uuid.uuid4().hex[:6]}"
    res = await _drive(tid, {
        "user_input": "M01's heated bed won't reach the target temperature — I think the thermistor is faulty.",
        "current_user_id": "E01", "email_dry_run": True}, resumes=[])
    ar = res.get("action_result") or {}
    print(f"\n[troubleshoot->technician] needs_technician={(res.get('diagnosis') or {}).get('needs_technician')} "
          f"action={ar.get('action')}\n    {res.get('final_response')}")
    if ar.get("action") == "technician_assigned":
        _cleanup_incident(ar["incident_id"], ar.get("assignee"),
                          (ar.get("slot") or {}).get("date"),
                          ar.get("assignee_role") == "Supervisor")
        print(f"    cleanup: removed {ar['incident_id']} + freed slot")

    # ---- troubleshoot -> self (decider interrupt -> self_action interrupt) ----
    print("\n" + "=" * 70)
    tid = f"self-{uuid.uuid4().hex[:6]}"
    res = await _drive(tid, {
        "user_input": "M03 prints aren't sticking to the bed; the first layer won't adhere.",
        "current_user_id": "E01", "email_dry_run": True}, resumes=["self", "complete"])
    ar = res.get("action_result") or {}
    print(f"\n[troubleshoot->self] needs_technician={(res.get('diagnosis') or {}).get('needs_technician')} "
          f"decision={res.get('decision_path')} action={ar.get('action')}\n    {res.get('final_response')}")
    if ar.get("action") == "self_resolved" and ar.get("incident_id"):
        _cleanup_incident(ar["incident_id"])
        print(f"    cleanup: removed {ar['incident_id']}")
    elif ar.get("action") == "technician_assigned":   # if diagnosis required a tech
        _cleanup_incident(ar["incident_id"], ar.get("assignee"),
                          (ar.get("slot") or {}).get("date"),
                          ar.get("assignee_role") == "Supervisor")
        print(f"    (routed to technician) cleanup: removed {ar['incident_id']}")

    print("\n" + "=" * 70 + "\nDONE")


if __name__ == "__main__":
    asyncio.run(main())
