"""
targets.py — per-dataset "targets": run the slice of the workflow each dataset
measures, returning a plain dict the evaluators read. These CALL the existing nodes
(no runtime changes). Used by run_eval.py via langsmith.aevaluate.

Server/quota needs:
  troubleshoot, manage -> HTTP MCP server up (RAG/DB tools), Groq only
                          (troubleshoot runs diagnosis_node; manage runs manage_resolve —
                          neither invokes a Gemini node)
  retrieval            -> local embedder + reranker (no MCP)
  sql                  -> Groq + Gemini (the text_to_sql_reviewer); SQL run on a direct
                          read-only conn  (the ONLY eval target that uses Gemini)
  routing, safety      -> Groq only
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in ("agents", "rag", "mcp_server", "synthetic_data/tables"):
    sys.path.insert(0, str(ROOT / p))


def _chunks_text(chunks) -> str:
    return "\n\n".join((c.get("text") or "") for c in (chunks or []))


def _diag_text(dx: dict) -> str:
    return (f"root_cause: {dx.get('root_cause')}\n"
            f"fix_steps: {dx.get('fix_steps')}\n"
            f"needs_technician: {dx.get('needs_technician')}\n"
            f"parts_needed: {dx.get('parts_needed')}\n"
            f"safety_notes: {dx.get('safety_notes')}")


async def target_troubleshoot(inputs: dict) -> dict:
    from nodes.diagnosis import diagnosis_node
    state = {"machine_id": inputs["machine_id"], "mvc_code": inputs["mvc_code"],
             "symptom": inputs["symptom"]}
    out = await diagnosis_node(state)
    rc = out.get("retrieved_context") or {}
    dx = out.get("diagnosis") or {}
    return {"diagnosis": dx, "diagnosis_text": _diag_text(dx),
            "context_text": _chunks_text(rc.get("manual")) + "\n\n" + _chunks_text(rc.get("safety")),
            "needs_technician": dx.get("needs_technician")}


async def target_retrieval(inputs: dict) -> dict:
    import retriever
    if inputs.get("mvc_code"):
        chunks = retriever.user_manual_retrieval(inputs["query"], inputs["mvc_code"], inputs.get("k", 5))
    else:
        chunks = retriever.safety_retrieval(inputs["query"], inputs.get("k", 2))
    retrieved = [{"source_file": c["metadata"].get("source_file"),
                  "page_start": c["metadata"].get("page_start"),
                  "page_end": c["metadata"].get("page_end")} for c in chunks]
    return {"retrieved": retrieved}


async def target_sql(inputs: dict) -> dict:
    from nodes.analytics import analytics_generate
    from nodes.text_to_sql_reviewer import text_to_sql_reviewer_node
    from safety import get_readonly_connection, validate_select_sql

    state = {"user_input": inputs["question"]}
    state.update(analytics_generate(state))
    review = text_to_sql_reviewer_node(state)
    state.update(review)
    agent_sql = (state.get("sql_plan") or {}).get("sql")
    approved = bool((state.get("sql_review") or {}).get("approved"))

    rows, readonly_ok, err = [], False, None
    if agent_sql:
        try:
            safe_sql = validate_select_sql(agent_sql)   # raises on write/phone/multi
            readonly_ok = True
            conn = get_readonly_connection(); cur = conn.cursor()
            cur.execute(safe_sql); rows = [list(map(str, r)) for r in cur.fetchall()]; conn.close()
        except Exception as e:
            err = str(e)[:200]
    return {"agent_sql": agent_sql, "review_approved": approved, "readonly_ok": readonly_ok,
            "rows": rows, "result_text": str(rows)[:1000], "error": err}


async def target_routing(inputs: dict) -> dict:
    from nodes.supervisor import supervisor_node
    out = supervisor_node({"user_input": inputs["utterance"]})
    return {"intent": out.get("intent")}


async def target_safety(inputs: dict) -> dict:
    from nodes.input import input_node
    out = input_node({"user_input": inputs["utterance"]})
    return {"input_safe": out.get("input_safe"), "guard_reason": out.get("guard_reason", "")}


async def target_manage(inputs: dict) -> dict:
    from nodes.manage_incident import manage_resolve
    state = {"user_input": inputs["utterance"]}
    if inputs.get("incident_id"):
        state["user_input"] = f"{inputs['utterance']} (incident {inputs['incident_id']})"
    out = await manage_resolve(state)
    plan = out.get("manage_plan") or {}
    return {"action": plan.get("action"), "requires_approval": bool(out.get("requires_approval")),
            "needs_clarification": bool(out.get("needs_clarification")),
            "plan_summary": plan.get("plan_summary", "")}


TARGETS = {
    "troubleshoot_cases.jsonl": target_troubleshoot,
    "retrieval_labels.jsonl": target_retrieval,
    "sql_cases.jsonl": target_sql,
    "routing_cases.jsonl": target_routing,
    "safety_redteam.jsonl": target_safety,
    "manage_cases.jsonl": target_manage,
}
