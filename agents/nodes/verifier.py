"""
verifier.py — Verifier Agent node (independent RAG-triad + safety judge).

Takes the Diagnosis plus the CONTEXT it was built from (retrieved manual/safety
passages + db_facts) and the user's SYMPTOM, and judges two relationships:
  context  vs query   -> context_relevant
  diagnosis vs context -> grounded;  diagnosis vs query -> answer_relevant; + safe
No tools — it judges the evidence already in state. On failure the graph loops back
to Diagnosis with the issues (capped at VERIFY_MAX_ATTEMPTS).

LLM: Gemini 2.5 Flash-Lite (independent — a different model family than the diagnoser).
Prompt: prompts/verifier.py (VERIFIER_SYSTEM, versioned).
Input  (reads state): symptom, retrieved_context, db_facts, diagnosis.
Output (writes state): verdict (Verdict dict), verify_attempts (incremented),
       prompt_versions["verifier"].
Structured output: Pydantic `Verdict` via with_structured_output.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agents/ on path
import config
from llms import get_judge
from schemas import Verdict
from prompts.verifier import VERIFIER_SYSTEM, VERIFIER_SYSTEM_VERSION

from langchain_core.messages import HumanMessage, SystemMessage

_MAX_CHUNK_CHARS = 1200


def _fmt_chunks(chunks) -> str:
    if not chunks:
        return "(none)"
    return "\n".join(
        f"- [{c.get('source_file')} p{c.get('page_start')}-{c.get('page_end')}] "
        f"{(c.get('text') or '')[:_MAX_CHUNK_CHARS]}"
        for c in chunks
    )


def _build_input(symptom, retrieved_context, db_facts, diagnosis) -> str:
    manual = (retrieved_context or {}).get("manual", [])
    safety = (retrieved_context or {}).get("safety", [])
    return (
        f"SYMPTOM (user query): {symptom}\n\n"
        f"CONTEXT — manual passages:\n{_fmt_chunks(manual)}\n\n"
        f"CONTEXT — safety passages:\n{_fmt_chunks(safety)}\n\n"
        f"CONTEXT — DB facts:\n"
        f"- overdue: {db_facts.get('overdue')}\n"
        f"- recent services: {db_facts.get('maintenance_history')}\n"
        f"- prior incidents: {db_facts.get('incidents')}\n\n"
        f"DIAGNOSIS to judge:\n"
        f"- root_cause: {diagnosis.get('root_cause')}\n"
        f"- evidence: {diagnosis.get('evidence')}\n"
        f"- fix_steps: {diagnosis.get('fix_steps')}\n"
        f"- needs_technician: {diagnosis.get('needs_technician')}\n"
        f"- parts_needed: {diagnosis.get('parts_needed')}\n"
        f"- safety_notes: {diagnosis.get('safety_notes')}"
    )


def verifier_node(state: dict) -> dict:
    """Judge the diagnosis; return {verdict, verify_attempts, prompt_versions}."""
    human = _build_input(
        state.get("symptom", ""),
        state.get("retrieved_context", {}),
        state.get("db_facts", {}),
        state.get("diagnosis", {}),
    )
    verdict = get_judge().with_structured_output(Verdict).invoke([
        SystemMessage(content=VERIFIER_SYSTEM),
        HumanMessage(content=human),
    ])
    attempts = state.get("verify_attempts", 0) + 1
    exhausted = (not verdict.approved) and attempts >= config.VERIFY_MAX_ATTEMPTS
    versions = dict(state.get("prompt_versions", {}))
    versions["verifier"] = VERIFIER_SYSTEM_VERSION
    return {
        "verdict": verdict.model_dump(),
        "verify_attempts": attempts,
        "verifier_exhausted": exhausted,
        "prompt_versions": versions,
    }


# === SELF-TEST — python agents/nodes/verifier.py  (needs GOOGLE key; no servers) ===
if __name__ == "__main__":
    context = {
        "manual": [{
            "text": "The heated bed uses a thermistor to read its temperature. If the "
                    "bed does not reach the target temperature, check the thermistor "
                    "wiring and the heater cartridge for faults.",
            "source_file": "lulzbot_mini_user_manual.pdf", "page_start": 50, "page_end": 51,
        }],
        "safety": [{
            "text": "The heated bed reaches high temperatures; allow it to cool and use "
                    "gloves before handling.",
            "source_file": "niosh_safe_3d_printing_2024-103.pdf", "page_start": 10, "page_end": 10,
        }],
    }
    db_facts = {"overdue": {"overdue": False}, "maintenance_history": [], "incidents": []}
    symptom = "the heated bed won't reach the target temperature"

    grounded_dx = {
        "root_cause": "Heated bed thermistor fault (faulty/disconnected thermistor wiring)",
        "evidence": ["manual p50-51: check the thermistor wiring if the bed doesn't reach temperature"],
        "fix_steps": ["Allow the bed to cool", "Check/reseat the thermistor wiring",
                      "Replace the thermistor if faulty"],
        "needs_technician": True, "parts_needed": ["thermistor"],
        "safety_notes": ["Allow the bed to cool and wear gloves before handling"],
        "retrieval_confidence": "high",
    }
    ungrounded_dx = {
        "root_cause": "The part-cooling fan has failed",
        "evidence": [], "fix_steps": ["Replace the part-cooling fan"],
        "needs_technician": True, "parts_needed": ["cooling fan"],
        "safety_notes": [], "retrieval_confidence": "high",
    }

    for label, dx in [("grounded", grounded_dx), ("ungrounded (fan not in context)", ungrounded_dx)]:
        out = verifier_node({"symptom": symptom, "retrieved_context": context,
                             "db_facts": db_facts, "diagnosis": dx})
        v = out["verdict"]
        print(f"\n[{label}] approved={v['approved']} score={v['score']}")
        print(f"   context_relevant={v['context_relevant']} grounded={v['grounded']} "
              f"answer_relevant={v['answer_relevant']} safe={v['safe']}")
        print(f"   issues: {v['issues']}")
