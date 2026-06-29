"""
intake.py — Intake Agent node (troubleshoot entry point).

Ensures we have a VALID machine + a symptom before diagnosis. The LLM extracts
machine_id + symptom from the text (merging anything gathered earlier); the node
validates the machine via get_machine — resolving mvc_code/status, and raising a
clarification when the machine is unknown or decommissioned. Resolved -> Diagnosis.

LLM: Groq Llama 3.3 70B (reasoner).
Tool: get_machine.
Prompt: prompts/intake.py (INTAKE_SYSTEM, versioned).
Input  (reads state): user_input (+ carried machine_id/symptom on resume).
Output (writes state): machine_id, mvc_code, machine_status, symptom,
       needs_clarification, clarification_question, prompt_versions["intake"].
Structured output: Pydantic `Intake` via with_structured_output (mvc_code filled
       by the node from get_machine, not the LLM).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agents/ on path
import clarify
import mcp_client
from llms import get_reasoner
from schemas import Intake
from prompts.intake import INTAKE_SYSTEM, INTAKE_SYSTEM_VERSION

from langchain_core.messages import HumanMessage, SystemMessage


async def _get_machine(machine_id: str) -> dict:
    tools = await mcp_client.get_all_tools()
    tool = next(t for t in tools if t.name == "get_machine")
    return mcp_client.parse_tool_result(await tool.ainvoke({"machine_id": machine_id}))


def _result(machine_id, mvc_code, machine_status, symptom,
            needs_clarification, question, versions) -> dict:
    return {
        "machine_id": machine_id,
        "mvc_code": mvc_code,
        "machine_status": machine_status,
        "symptom": symptom,
        "needs_clarification": needs_clarification,
        "clarification_question": question,
        "prompt_versions": versions,
    }


async def intake_node(state: dict) -> dict:
    user_input = state.get("user_input", "")
    versions = dict(state.get("prompt_versions", {}))
    versions["intake"] = INTAKE_SYSTEM_VERSION
    carried_machine = state.get("machine_id")
    carried_symptom = state.get("symptom")

    human = f"User message: {user_input}"
    if carried_machine or carried_symptom:
        human += (f"\n\nAlready gathered — machine_id: {carried_machine}, "
                  f"symptom: {carried_symptom}")

    extracted = get_reasoner(structured=Intake).invoke([
        SystemMessage(content=INTAKE_SYSTEM),
        HumanMessage(content=human),
    ])

    # The LLM read the reply's intent: bail / change-topic -> stop cleanly (the regex
    # fast-path in the graph wrapper catches obvious "ok"/"cancel"; this catches the rest).
    if extracted.user_quit:
        return {"needs_clarification": False, "clarify_abandoned": True,
                "final_response": clarify.bailed(), "prompt_versions": versions}

    machine_id = extracted.machine_id or carried_machine
    symptom = extracted.symptom or carried_symptom
    mvc_code = machine_status = None

    stuck = extracted.user_stuck   # LLM-judged "I don't know" -> guide instead of re-asking

    # Validate the machine (resolve mvc_code/status) when one was given.
    if machine_id:
        machine = await _get_machine(machine_id)
        if not machine.get("exists"):
            q = f"I couldn't find machine {machine_id}. Could you confirm the machine id?"
            return _result(machine_id, None, None, symptom, True,
                           clarify.guide(q, "machine_id") if stuck else q, versions)
        machine_id = machine.get("machine_id", machine_id)   # normalized
        mvc_code = machine.get("mvc_code")
        machine_status = machine.get("status")
        if machine_status == "Decommissioned":
            q = (f"Machine {machine_id} ({machine.get('model_name')}) is already "
                 f"decommissioned — is the machine number correct?")
            return _result(machine_id, mvc_code, machine_status, symptom, True,
                           clarify.guide(q, "machine_id") if stuck else q, versions)

    # Ask for whatever is still missing.
    if not machine_id:
        q = "Which machine is this? Please give its id, like M01."
        return _result(None, None, None, symptom, True,
                       clarify.guide(q, "machine_id") if stuck else q, versions)
    if not symptom:
        q = f"What's the problem with {machine_id}?"
        return _result(machine_id, mvc_code, machine_status, None, True,
                       clarify.guide(q, "symptom") if stuck else q, versions)

    # Resolved -> Diagnosis.
    return _result(machine_id, mvc_code, machine_status, symptom, False, None, versions)


# === SELF-TEST — needs GROQ key AND the HTTP server running (get_all_tools
# connects to both servers):
#     python mcp_server/server.py http        # (separate terminal)
#     python agents/nodes/intake.py
# Read-only (get_machine) — no DB mutation.
# ============================================================================
if __name__ == "__main__":
    import asyncio

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "synthetic_data" / "tables"))
    from db_connection import get_connection

    c = get_connection(); cur = c.cursor()
    cur.execute("SELECT machine_id FROM machines WHERE status='Decommissioned' LIMIT 1")
    row = cur.fetchone(); dec = row[0] if row else "M20"; c.close()

    async def _show(label, state):
        out = await intake_node(state)
        flag = "CLARIFY" if out["needs_clarification"] else "RESOLVED"
        print(f"\n[{label}] {flag}")
        print(f"   machine={out['machine_id']} mvc={out['mvc_code']} status={out['machine_status']} "
              f"symptom={out['symptom']!r}")
        if out["needs_clarification"]:
            print(f"   Q: {out['clarification_question']}")
        return out

    async def _main():
        await _show("valid", {"user_input": "M01's bed won't heat to the target temperature"})
        await _show("no machine id", {"user_input": "my printer won't heat up"})
        await _show("no symptom", {"user_input": "there's an issue with M01"})
        await _show("unknown machine", {"user_input": "M99 keeps clogging"})
        await _show(f"decommissioned ({dec})", {"user_input": f"{dec} won't print"})
        # resume: symptom carried from a prior turn, user now gives the machine
        await _show("resume (carried symptom + 'M01')",
                    {"user_input": "M01", "symptom": "bed won't heat"})

    asyncio.run(_main())
