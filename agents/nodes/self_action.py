"""
self_action.py — Self Action Agent (operator self-fix path).

Reached when the Decider returns "self". Mechanical (no LLM): it presents the
already-verified guidance (Diagnosis fix_steps + safety_notes) with a two-choice
ask, and on the operator's choice either:
  * "complete"   -> logs a SELF-RESOLVED incident (create_incident -> update_incident
                    with the operator as assignee, comment "Self-Action by the
                    operator", closed; no schedule booking), then -> Output; or
  * "technician" -> writes NOTHING and routes to Technician Action.

The incident is created ONLY on "complete". The guidance shown is reused from the
Diagnosis (grounded + Verifier-approved) — no re-retrieval.

Tools: create_incident, update_incident. No prompt / no LLM.
Input  (reads state): diagnosis, machine_id, symptom, current_user_id,
       self_action_choice ("complete" | "technician", from the interrupt).
Output (writes state): action_result.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agents/ on path
import mcp_client
from utils import streaming


async def _call(name: str, args: dict):
    streaming.emit_tool(name, args)
    tools = await mcp_client.get_all_tools()
    tool = next(t for t in tools if t.name == name)
    return mcp_client.parse_tool_result(await tool.ainvoke(args))


def self_action_message(diagnosis: dict) -> str:
    """Operator-facing guidance + the two-choice ask (surfaced via interrupt)."""
    steps = (diagnosis or {}).get("fix_steps") or []
    safety = (diagnosis or {}).get("safety_notes") or []
    lines = ["Here's how to fix it yourself:"]
    lines += [f"  {i}. {step}" for i, step in enumerate(steps, 1)]
    if safety:
        lines.append("\nSafety precautions:")
        lines += [f"  - {note}" for note in safety]
    lines.append("\nWhen you're done, choose: [Complete & close service request] "
                 "or [Book a technician instead].")
    return "\n".join(lines)


async def self_action_execute(state: dict) -> dict:
    """Act on the operator's choice: log a self-resolved incident, or escalate."""
    choice = state.get("self_action_choice", "complete")
    if choice == "technician":
        return {"action_result": {"action": "escalate_to_technician"}}

    diagnosis = state.get("diagnosis", {})
    operator = state.get("current_user_id")
    resolution = "; ".join(diagnosis.get("fix_steps") or []) or diagnosis.get("root_cause", "")

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
    await _call("update_incident", {
        "incident_id": incident_id,
        "technician_comments": "Self-Action by the operator",
        "close": True,
        "assignee_id": operator,
    })
    return {"action_result": {"action": "self_resolved",
                              "incident_id": incident_id, "assignee": operator}}


# === SELF-TEST — needs the HTTP server up (get_all_tools connects to both):
#     python mcp_server/server.py http      # (separate terminal)
#     python agents/nodes/self_action.py
# Create-then-clean; no LLM key needed.
# ============================================================================
if __name__ == "__main__":
    import asyncio

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "synthetic_data" / "tables"))
    from db_connection import get_connection

    diagnosis = {
        "root_cause": "Bed not level / Z-offset too high",
        "fix_steps": ["Re-run bed leveling (G29)", "Adjust Z-offset in small steps"],
        "safety_notes": ["Let the bed cool before handling"],
    }
    print(self_action_message(diagnosis), "\n")

    base = {"machine_id": "M01", "current_user_id": "E01",
            "symptom": "prints aren't sticking", "diagnosis": diagnosis}

    async def _main():
        # "complete" -> self-resolved
        res = await self_action_execute({**base, "self_action_choice": "complete"})
        print("complete   ->", res["action_result"])
        inc = res["action_result"].get("incident_id")
        if inc:
            c = get_connection(); cur = c.cursor(dictionary=True)
            cur.execute("SELECT reported_by, technician_id, technician_comments, "
                        "work_date, incident_closure_date FROM incidents WHERE incident_id=%s", (inc,))
            print("  row:", cur.fetchone(), "(expect reported_by=E01, technician_id=E01, work_date=None)")
            cur2 = c.cursor(); cur2.execute("DELETE FROM incidents WHERE incident_id=%s", (inc,))
            c.commit(); c.close()
            print(f"  cleanup -> deleted {inc}")

        # "technician" -> escalate, no write
        res2 = await self_action_execute({**base, "self_action_choice": "technician"})
        print("technician ->", res2["action_result"])

    asyncio.run(_main())
