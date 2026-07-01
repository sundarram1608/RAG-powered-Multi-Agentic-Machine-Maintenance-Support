"""
output.py — Output Agent (the single voice): compose the final user-facing reply.

Grounding = Option A. Fact-heavy paths are rendered by TEMPLATES here (exact, from
state) so ids/names/dates/counts can't be hallucinated; the LLM is used ONLY for
`general` (capability/greeting) and `analytics` (summarize rows, exact quoting).
A regex PII scrub runs on the final text (belt-and-suspenders).

LLM: Groq Llama 3.3 70B (general + analytics + advice modes). No tools.
Prompt: prompts/output.py (OUTPUT_SYSTEM, versioned).
Input  (reads state): intent, input_safe, guard_reason, user_input, diagnosis,
       action_result, manage_plan, sql_result, verifier_exhausted,
       clarify_abandoned (+ its pre-composed final_response), and (advice mode)
       advice_topic + retrieved_context.
Output (writes state): final_response, prompt_versions["output"].
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agents/ on path
from llms import get_reasoner
from prompts.output import OUTPUT_SYSTEM, OUTPUT_SYSTEM_VERSION

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"\b\d{7,}\b")


def _scrub_pii(text: str) -> str:
    """Final guard: strip any email / long digit sequence (phone) from the reply."""
    text = _EMAIL_RE.sub("[redacted]", text)
    return _PHONE_RE.sub("[redacted]", text)


def _llm(mode: str, body: str) -> str:
    reply = get_reasoner().invoke([
        SystemMessage(content=OUTPUT_SYSTEM),
        HumanMessage(content=f"MODE = {mode}\n{body}"),
    ])
    return reply.content


# --- templated (deterministic) renderers for the fact-heavy paths ---

# Templated resolution replies. Each: (1) ANSWERS "can I fix it myself?", (2) gives the
# REASONING (the verified diagnosis' root cause), then (3) ties that to the ACTION taken.
def _self_resolved(state: dict) -> str:
    # The fix steps + reasoning were already shown in the self-fix guidance
    # (self_action_message) — so this is just the closing CONFIRMATION, not a repeat.
    inc = (state.get("action_result") or {}).get("incident_id")
    return f"Done — glad that sorted it! I've logged and closed it as {inc} (self-resolved)."


def _technician(state: dict) -> str:
    dx = state.get("diagnosis") or {}
    ar = state.get("action_result") or {}
    inc, role, emp = ar.get("incident_id"), ar.get("assignee_role"), ar.get("assignee")
    slot = ar.get("slot") or {}
    root = (dx.get("root_cause") or "").strip()
    parts = ", ".join(dx.get("parts_needed") or [])

    # (1) answer + (2) reasoning
    if state.get("verifier_exhausted"):
        head = ("This one isn't a safe self-fix — I couldn't confirm a reliable fix from "
                "the manuals, so it needs a technician to assess on site.")
    else:
        why = root or "it needs on-site work"
        tail = f", and a part is needed ({parts})" if parts else ""
        head = f"No — this isn't one to fix yourself: {why}{tail}, so it needs a technician."
    # (3) the action that follows from it
    act = (f"I've logged it as {inc} and assigned {role} {emp} for "
           f"{slot.get('date')} ({slot.get('availability_slot')}); you'll be notified.")
    if ar.get("escalated"):
        act += " (No technician was free in the window, so it's escalated to a supervisor.)"
    return head + " " + act


def _manage(state: dict) -> str:
    ar = state.get("action_result") or {}
    plan = state.get("manage_plan") or {}
    inc = plan.get("incident_id") or (ar.get("result") or {}).get("incident_id")
    action = ar.get("action")
    if action == "cancelled":
        return "Okay — cancelled. No changes were made."
    if action == "close":
        return f"Incident {inc} has been closed."
    if action == "update_comment":
        return f"Incident {inc} has been updated."
    if action == "assign":
        r = ar.get("result") or {}; slot = r.get("booked_slot") or {}
        return (f"{r.get('employee_id')} has been assigned to incident {inc} for "
                f"{slot.get('date')} ({slot.get('availability_slot')}).")
    return plan.get("plan_summary") or "Done."


def output_node(state: dict) -> dict:
    intent = state.get("intent")
    action = (state.get("action_result") or {}).get("action")

    if state.get("input_safe") is False:                       # refusal (templated)
        text = state.get("guard_reason") or "I can't help with that request."
    elif state.get("clarify_abandoned"):                       # gave up after re-ask cap (templated)
        text = state.get("final_response") or "I can't proceed without that information."
    elif intent == "general":                                  # LLM
        text = _llm("general", f"User question: {state.get('user_input', '')}")
    elif intent == "analytics":                                # LLM (exact quoting)
        text = _llm("analytics", f"User question: {state.get('user_input', '')}\n"
                                 f"Result rows (JSON): {state.get('sql_result')}")
    elif intent == "advice":                                   # LLM (grounded in safety guide)
        text = _llm("advice", f"Question / topic: {state.get('advice_topic') or state.get('user_input', '')}\n"
                              f"Safety-guide passages: {state.get('retrieved_context')}")
    elif intent == "manage_incident":                          # templated
        text = _manage(state)
    elif action == "self_resolved":                            # templated
        text = _self_resolved(state)
    elif action == "no_assignee":                              # templated
        inc = (state.get("action_result") or {}).get("incident_id")
        text = (f"Logged as {inc}, but no technician or supervisor is currently "
                f"available. We'll follow up as soon as someone frees up.")
    elif action == "error":                                    # templated
        text = "Sorry — something went wrong while completing that. Please try again."
    else:                                                       # technician dispatched (templated)
        text = _technician(state)

    versions = dict(state.get("prompt_versions", {}))
    versions["output"] = OUTPUT_SYSTEM_VERSION
    final = _scrub_pii(text)
    # Append the assistant turn to the append-only history so the next turn's gates
    # (Input/Supervisor) + Analytics coder can resolve follow-ups (see history.py).
    return {"final_response": final, "prompt_versions": versions,
            "messages": [AIMessage(content=final)]}


# === SELF-TEST — python agents/nodes/output.py  (needs GROQ key for general+analytics) ===
if __name__ == "__main__":
    dx = {"root_cause": "Bed not level", "fix_steps": ["Re-run bed leveling (G29)", "Adjust Z-offset"],
          "safety_notes": ["Let the bed cool first"]}
    cases = {
        "refusal": {"input_safe": False, "guard_reason": "I can only help with FDM printer maintenance and service."},
        "general": {"intent": "general", "user_input": "what can you do?"},
        "analytics": {"intent": "analytics", "user_input": "how many incidents are open?",
                      "sql_result": {"ok": True, "row_count": 1, "rows": [{"open_incidents": 4}]}},
        "self_resolved": {"intent": "troubleshoot", "diagnosis": dx,
                          "action_result": {"action": "self_resolved", "incident_id": "inc_26"}},
        "technician": {"intent": "troubleshoot",
                       "action_result": {"action": "technician_assigned", "incident_id": "inc_27",
                                         "assignee": "E13", "assignee_role": "Technician",
                                         "slot": {"date": "2026-06-17", "availability_slot": "09:00-11:00"},
                                         "escalated": False}},
        "exhausted->tech": {"intent": "troubleshoot", "verifier_exhausted": True,
                            "action_result": {"action": "technician_assigned", "incident_id": "inc_28",
                                             "assignee": "E04", "assignee_role": "Supervisor",
                                             "slot": {"date": "2026-12-01", "availability_slot": "10:00-11:00"},
                                             "escalated": True}},
        "no_assignee": {"intent": "troubleshoot",
                        "action_result": {"action": "no_assignee", "incident_id": "inc_29"}},
        "manage_close": {"intent": "manage_incident", "manage_plan": {"incident_id": "inc_8"},
                         "action_result": {"action": "close", "result": {"ok": True, "incident_id": "inc_8"}}},
    }
    for label, st in cases.items():
        out = output_node(st)["final_response"]
        pii = bool(_EMAIL_RE.search(out) or _PHONE_RE.search(out))
        print(f"\n===== {label} (PII leak: {pii}) =====\n{out}")
