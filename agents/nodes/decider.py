"""
decider.py — Decider Agent node.

Reached ONLY for operator-fixable diagnoses (needs_technician == False). The graph
asks the operator (via interrupt) — using `decider_question(diagnosis)` — whether
they want guided self-fix or a technician; this node interprets the reply into a
Decision and routes: self -> Self Action, technician -> Technician Action.

LLM: Groq Llama 3.3 70B (interpret the free-text reply). No tools.
Prompt: prompts/decider.py (DECIDER_SYSTEM, versioned).
Input  (reads state): user_input (the operator's reply); diagnosis (for the ask).
Output (writes state): decision_path, needs_clarification, clarification_question,
       prompt_versions["decider"].
Structured output: Pydantic `Decision` via with_structured_output.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agents/ on path
from llms import get_reasoner
from schemas import Decision
from prompts.decider import DECIDER_SYSTEM, DECIDER_SYSTEM_VERSION

from langchain_core.messages import HumanMessage, SystemMessage


def decider_question(diagnosis: dict) -> str:
    """The operator-facing ask (surfaced by the graph via interrupt)."""
    root_cause = (diagnosis or {}).get("root_cause", "the issue")
    return (f"This looks like something you can safely fix yourself: {root_cause}. "
            f"Would you like step-by-step guidance to do it yourself, or should I "
            f"assign a technician?")


def decider_node(state: dict) -> dict:
    """Interpret the operator's reply into a Decision."""
    reply = state.get("user_input", "")
    decision = get_reasoner().with_structured_output(Decision).invoke([
        SystemMessage(content=DECIDER_SYSTEM),
        HumanMessage(content=f"Operator's reply: {reply}"),
    ])
    versions = dict(state.get("prompt_versions", {}))
    versions["decider"] = DECIDER_SYSTEM_VERSION
    return {
        "decision_path": decision.path,
        "needs_clarification": decision.needs_clarification,
        "clarification_question": decision.question,
        "prompt_versions": versions,
    }


# === SELF-TEST — python agents/nodes/decider.py  (needs GROQ key; no servers) ===
if __name__ == "__main__":
    print("ASK:", decider_question({"root_cause": "Bed not level / Z-offset too high"}), "\n")
    replies = [
        "I'll do it myself",
        "please send a technician",
        "I'm not comfortable doing that",
        "ok",
    ]
    for reply in replies:
        out = decider_node({"user_input": reply})
        print(f"  {reply!r:34} -> path={out['decision_path']} "
              f"clarify={out['needs_clarification']} q={out['clarification_question']}")
