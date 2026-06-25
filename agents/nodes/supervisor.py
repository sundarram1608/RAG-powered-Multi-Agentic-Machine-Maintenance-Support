"""
supervisor.py — Supervisor Agent node: intent router.

Classifies the (already-guarded) user turn into one of four routes and writes it
to state["intent"]. No tools. The graph uses `intent` to branch:
    troubleshoot -> Intake · analytics -> Analytics ·
    manage_incident -> Manage-Incident handler · general -> Output.

LLM: Groq Llama 3.3 70B (reasoner).
Prompt: prompts/supervisor.py (SUPERVISOR_SYSTEM, versioned).
Input  (reads state): user_input (else last message).
Output (writes state): intent, prompt_versions["supervisor"].
Structured output: Pydantic `Route` via with_structured_output.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agents/ on path
from llms import get_reasoner
from schemas import Route
from history import format_recent
from prompts.supervisor import SUPERVISOR_SYSTEM, SUPERVISOR_SYSTEM_VERSION

from langchain_core.messages import HumanMessage, SystemMessage


def _user_text(state: dict) -> str:
    if state.get("user_input"):
        return state["user_input"]
    messages = state.get("messages") or []
    return messages[-1].content if messages else ""


def supervisor_node(state: dict) -> dict:
    """Route the user turn; return {intent, prompt_versions}."""
    user_text = _user_text(state)
    # Prior context = history minus the current turn (already appended in api.start_turn).
    context = format_recent((state.get("messages") or [])[:-1], max_exchanges=5)
    human = ""
    if context:
        human += f"Recent conversation (for context only):\n{context}\n\n"
    human += f'Route this user message:\n\n"""\n{user_text}\n"""'

    llm = get_reasoner().with_structured_output(Route)
    result = llm.invoke([
        SystemMessage(content=SUPERVISOR_SYSTEM),
        HumanMessage(content=human),
    ])

    versions = dict(state.get("prompt_versions", {}))
    versions["supervisor"] = SUPERVISOR_SYSTEM_VERSION
    return {"intent": result.next, "prompt_versions": versions}


# === SELF-TEST — python agents/nodes/supervisor.py  (needs GROQ_API_KEY) ===
if __name__ == "__main__":
    cases = [
        ("M01's bed won't heat to target",        "troubleshoot"),
        ("How many incidents are still open?",    "analytics"),
        ("Mark incident inc_26 complete",         "manage_incident"),
        ("Assign a technician to inc_30",          "manage_incident"),
        ("What can you do?",                       "general"),
        ("Hi there",                               "general"),
    ]
    print(f"prompt_version = {SUPERVISOR_SYSTEM_VERSION}\n")
    print(f"{'expected':16} | {'got':16} | input")
    print("-" * 80)
    for text, expected in cases:
        got = supervisor_node({"user_input": text})["intent"]
        mark = "✓" if got == expected else "✗"
        print(f"{expected:16} | {got:16} | {mark} {text}")
