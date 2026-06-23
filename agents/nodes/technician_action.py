"""
technician_action.py — Technician Action Agent (dispatch path).

Reached when a technician is required: Diagnosis needs_technician=True, or
Decider->technician, or Self Action -> "book a technician instead". In ALL of
these the incident does NOT exist yet, so this node ALWAYS creates it, then
auto-assigns and notifies. Mechanical: no LLM, no approval, no interrupt — the
decision to dispatch was already made upstream.

Flow: create_incident -> find_available_technician (3-day hierarchy, else
supervisor escalation) -> book_technician_slot -> send_email (assignee + operator).
The assignee is a Technician, or a Supervisor on escalation; send_email picks the
template by the recipient's role. The incident stays OPEN (the assignee closes it
later via Manage Incident).

LLM: none. Tools: create_incident, find_available_technician, book_technician_slot,
     update_incident (allow-listed; unused at dispatch), send_email.
Input  (reads state): machine_id, symptom, diagnosis, current_user_id,
       optional booking_moment, email_dry_run.
Output (writes state): action_result.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agents/ on path
import mcp_client


async def _call(name: str, args: dict):
    tools = await mcp_client.get_all_tools()
    tool = next(t for t in tools if t.name == name)
    return mcp_client.parse_tool_result(await tool.ainvoke(args))


async def technician_action(state: dict) -> dict:
    diagnosis = state.get("diagnosis", {})
    operator = state.get("current_user_id")
    dry = state.get("email_dry_run", False)
    resolution = "; ".join(diagnosis.get("fix_steps") or []) or diagnosis.get("root_cause", "")

    # 1) Always create the incident (it doesn't exist yet on any dispatch path).
    created = await _call("create_incident", {
        "machine_id": state.get("machine_id"),
        "reported_by": operator,
        "user_complaint": state.get("symptom", ""),
        "agent_root_cause": diagnosis.get("root_cause", ""),
        "agentic_resolution": resolution,
    })
    if not created.get("ok"):
        return {"action_result": {"action": "error", "error": created.get("error")}}
    incident_id = created["incident_id"]

    # 2) Auto-assign: earliest technician over 3 days, else supervisor escalation.
    bm = state.get("booking_moment")
    proposal = await _call("find_available_technician",
                           {"booking_moment": bm} if bm else {})
    if proposal.get("available") is False:
        return {"action_result": {"action": "no_assignee", "incident_id": incident_id,
                                  "note": proposal.get("note")}}

    # 3) Book the slot (technician = UPDATE; supervisor = INSERT a Booked row).
    await _call("book_technician_slot", {
        "incident_id": incident_id,
        "employee_id": proposal["employee_id"],
        "date": proposal["date"],
        "availability_slot": proposal["availability_slot"],
    })

    # 4) Notify the assignee (technician/supervisor) and the operator.
    emails = []
    await _call("send_email", {"to_employee_id": proposal["employee_id"],
                               "incident_id": incident_id, "dry_run": dry})
    emails.append(proposal["employee_id"])
    if operator:
        await _call("send_email", {"to_employee_id": operator,
                                   "incident_id": incident_id, "dry_run": dry})
        emails.append(operator)

    return {"action_result": {
        "action": "technician_assigned",
        "incident_id": incident_id,
        "assignee": proposal["employee_id"],
        "assignee_role": proposal.get("assignee_role"),
        "slot": {"date": proposal["date"], "availability_slot": proposal["availability_slot"]},
        "escalated": proposal.get("escalated", False),
        "emails_sent": emails,
    }}


# === SELF-TEST — needs the HTTP server up (send_email + get_all_tools).
#     python mcp_server/server.py http      # (separate terminal)
#     python agents/nodes/technician_action.py
# Create-then-clean; email_dry_run=True (no real emails).
# ============================================================================
if __name__ == "__main__":
    import asyncio

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "synthetic_data" / "tables"))
    from db_connection import get_connection

    diagnosis = {"root_cause": "Hotend thermistor fault",
                 "fix_steps": ["Replace the thermistor"],
                 "safety_notes": ["Let the hotend cool first"]}
    base = {"machine_id": "M01", "current_user_id": "E01",
            "symptom": "the bed won't heat", "diagnosis": diagnosis, "email_dry_run": True}

    def _cleanup(incident_id, emp, date, supervisor):
        c = get_connection(); cur = c.cursor()
        if supervisor:   # escalation inserted a row -> delete it
            cur.execute("DELETE FROM technician_schedule WHERE `date`=%s AND employee_id=%s", (date, emp))
        else:            # technician slot -> free it
            cur.execute("UPDATE technician_schedule SET availability_status='Available' "
                        "WHERE `date`=%s AND employee_id=%s", (date, emp))
        cur.execute("DELETE FROM incidents WHERE incident_id=%s", (incident_id,))
        c.commit(); c.close()

    async def _run(label, state):
        res = (await technician_action(state))["action_result"]
        print(f"\n[{label}] {res}")
        if res["action"] == "technician_assigned":
            c = get_connection(); cur = c.cursor(dictionary=True)
            cur.execute("SELECT technician_id, work_date, work_slot, incident_closure_date "
                        "FROM incidents WHERE incident_id=%s", (res["incident_id"],))
            print("  incident:", cur.fetchone(), "(open, assignee set)"); c.close()
            _cleanup(res["incident_id"], res["assignee"], res["slot"]["date"],
                     res["assignee_role"] == "Supervisor")
            print("  cleanup done")

    async def _main():
        await _run("technician (auto)", base)
        await _run("supervisor escalation", {**base, "booking_moment": "2026-12-01 09:00:00"})

    asyncio.run(_main())
