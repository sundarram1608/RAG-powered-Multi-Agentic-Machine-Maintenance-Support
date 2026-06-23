"""
diagnosis.py — Diagnosis Agent node (core reasoner of the troubleshoot path).

ORCHESTRATED (not free ReAct): the node deterministically gathers a fixed evidence
bundle (RAG manual + safety, plus DB facts), the LLM synthesizes a grounded
Diagnosis, then corrective-RAG re-queries the manual (sharpened with the
hypothesised root_cause) while retrieval_confidence is "low" (capped). Finally it
looks up stock for any parts_needed. Output feeds the Verifier.

LLM: Groq Llama 3.3 70B (synthesis only; tools are called by the node).
Tools: user_manual_retrieval, safety_retrieval, get_overdue_status,
       get_maintenance_history, get_incident_history, check_inventory.
Prompt: prompts/diagnosis.py (DIAGNOSIS_SYSTEM, versioned).
Input  (reads state): machine_id, mvc_code, symptom (from Intake).
Output (writes state): diagnosis, retrieved_context, db_facts,
       prompt_versions["diagnosis"].
Structured output: Pydantic `Diagnosis` via with_structured_output.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agents/ on path
import config
import mcp_client
from llms import get_reasoner
from schemas import Diagnosis
from prompts.diagnosis import DIAGNOSIS_SYSTEM, DIAGNOSIS_SYSTEM_VERSION

from langchain_core.messages import HumanMessage, SystemMessage

_MAX_CHUNK_CHARS = 1500   # cap each passage in the prompt to control token size

# Operator-swappable consumables (do NOT force a technician for these).
_CONSUMABLE_KEYWORDS = ("filament", "glue", "spool", "pla", "abs", "petg", "nylon")


def _is_consumable(part_name: str, inventory_rows: list) -> bool:
    """True if the part is an operator-swappable consumable (by inventory category
    or name) — anything else is treated as a hardware spare (-> technician)."""
    if any((row.get("category") or "").lower() == "consumable" for row in inventory_rows):
        return True
    name = (part_name or "").lower()
    return any(kw in name for kw in _CONSUMABLE_KEYWORDS)


def _fmt_chunks(chunks: list) -> str:
    if not chunks:
        return "(none)"
    return "\n".join(
        f"- [{c.get('source_file')} p{c.get('page_start')}-{c.get('page_end')}] "
        f"{(c.get('text') or '')[:_MAX_CHUNK_CHARS]}"
        for c in chunks
    )


def _build_evidence(symptom, machine_id, mvc_code, manual, safety, db_facts) -> str:
    return (
        f"Machine: {machine_id} (version {mvc_code})\n"
        f"Symptom: {symptom}\n\n"
        f"MANUAL passages:\n{_fmt_chunks(manual)}\n\n"
        f"SAFETY passages:\n{_fmt_chunks(safety)}\n\n"
        f"DB facts:\n"
        f"- overdue status: {db_facts['overdue']}\n"
        f"- recent preventive services: {db_facts['maintenance_history']}\n"
        f"- prior incidents: {db_facts['incidents']}"
    )


def _synthesize(symptom, machine_id, mvc_code, manual, safety, db_facts,
                prior_issues=None) -> Diagnosis:
    human = _build_evidence(symptom, machine_id, mvc_code, manual, safety, db_facts)
    if prior_issues:
        human += ("\n\nA previous attempt was REJECTED by the verifier for these "
                  f"reasons — address them: {prior_issues}")
    return get_reasoner().with_structured_output(Diagnosis).invoke([
        SystemMessage(content=DIAGNOSIS_SYSTEM),
        HumanMessage(content=human),
    ])


async def diagnosis_node(state: dict) -> dict:
    machine_id = state.get("machine_id")
    mvc_code = state.get("mvc_code")
    symptom = state.get("symptom", "")
    versions = dict(state.get("prompt_versions", {}))
    versions["diagnosis"] = DIAGNOSIS_SYSTEM_VERSION

    tools = await mcp_client.get_all_tools()
    by_name = {t.name: t for t in tools}

    async def call(name, args, expect_list=False):
        return mcp_client.parse_tool_result(
            await by_name[name].ainvoke(args), expect_list=expect_list)

    # --- gather the evidence bundle concurrently ---
    manual, safety, overdue, history, incidents = await asyncio.gather(
        call("user_manual_retrieval", {"query": symptom, "mvc_code": mvc_code, "k": 5}, expect_list=True),
        call("safety_retrieval", {"query": symptom, "k": 2}, expect_list=True),
        call("get_overdue_status", {"machine_id": machine_id}),
        call("get_maintenance_history", {"machine_id": machine_id}, expect_list=True),
        call("get_incident_history", {"machine_id": machine_id}, expect_list=True),
    )
    db_facts = {"overdue": overdue, "maintenance_history": history, "incidents": incidents}

    # --- synthesize, with corrective-RAG re-query while confidence is low ---
    # On a verify-driven retry, fold the verifier's issues in so we self-correct.
    prior_issues = None
    verdict = state.get("verdict")
    if verdict and not verdict.get("approved"):
        prior_issues = verdict.get("issues")

    diagnosis = None
    for attempt in range(config.MAX_DIAGNOSIS_REQUERIES):
        diagnosis = _synthesize(symptom, machine_id, mvc_code, manual, safety, db_facts,
                                prior_issues=prior_issues)
        last = attempt == config.MAX_DIAGNOSIS_REQUERIES - 1
        if diagnosis.retrieval_confidence != "low" or last:
            break
        refined = f"{symptom}. {diagnosis.root_cause}"   # hypothesis-sharpened query
        manual = await call("user_manual_retrieval",
                            {"query": refined, "mvc_code": mvc_code, "k": 5}, expect_list=True)

    # --- post-synthesis: stock for any parts needed (for the Action path) ---
    parts_availability = {}
    for part in (diagnosis.parts_needed or []):
        parts_availability[part] = await call("check_inventory", {"part": part}, expect_list=True)
    db_facts["parts_availability"] = parts_availability

    dx = diagnosis.model_dump()
    # Deterministic safety backstop: a non-consumable hardware part always requires
    # a technician, even if the LLM marked it operator-fixable.
    if dx.get("parts_needed") and any(
        not _is_consumable(part, parts_availability.get(part, []))
        for part in dx["parts_needed"]
    ):
        dx["needs_technician"] = True

    return {
        "diagnosis": dx,
        "retrieved_context": {"manual": manual, "safety": safety},
        "db_facts": db_facts,
        "prompt_versions": versions,
    }


# === SELF-TEST — needs GROQ key AND the HTTP server running (get_all_tools
# connects to both servers). Read-only — no DB mutation.
#     python mcp_server/server.py http        # (separate terminal)
#     python agents/nodes/diagnosis.py
# ============================================================================
if __name__ == "__main__":
    async def _show(label, state):
        out = await diagnosis_node(state)
        d = out["diagnosis"]
        print(f"\n[{label}] confidence={d['retrieval_confidence']} "
              f"needs_technician={d['needs_technician']}")
        print(f"   root_cause: {d['root_cause']}")
        print(f"   fix_steps: {d['fix_steps']}")
        print(f"   parts_needed: {d['parts_needed']} | safety_notes: {len(d['safety_notes'])} "
              f"| evidence: {len(d['evidence'])}")
        print(f"   overdue: {out['db_facts']['overdue'].get('overdue')} "
              f"| parts_availability keys: {list(out['db_facts']['parts_availability'])}")

    async def _main():
        await _show("M01 bed won't heat",
                    {"machine_id": "M01", "mvc_code": "MVC02",
                     "symptom": "the bed won't heat to the target temperature"})
        await _show("M03 (overdue) prints not sticking",
                    {"machine_id": "M03", "mvc_code": "MVC01",
                     "symptom": "prints aren't sticking to the bed; first layer lifts"})

    asyncio.run(_main())
