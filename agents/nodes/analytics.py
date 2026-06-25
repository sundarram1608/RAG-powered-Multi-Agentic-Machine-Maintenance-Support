"""
analytics.py — Analytics Agent: Text-to-SQL coder (generate) + executor.

Two phases (one agent), with the Text-to-SQL Reviewer gating between them:
  analytics_generate : LLM — NL question (+ any prior critique) -> SqlPlan.
  analytics_execute  : mechanical — run the APPROVED SQL via run_readonly_query
                       (no LLM; the query is fixed and already reviewed).
Summarizing the rows into prose is the Output Agent's job (not here).

LLM (generate): Groq Llama 3.3 70B (reasoner). execute: no LLM.
Tools: run_readonly_query (execute phase only).
Prompt: prompts/analytics.py (ANALYTICS_CODER_SYSTEM, versioned).
Input  (reads state): user_input; on retry also sql_plan + sql_review/sql_result.
Output (writes state): generate -> sql_plan, analytics_attempts; execute -> sql_result;
                       prompt_versions["analytics"].
Structured output: Pydantic `SqlPlan` via with_structured_output.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agents/ on path
import config
import mcp_client
from llms import get_reasoner
from schemas import SqlPlan
from history import format_recent
from prompts.analytics import ANALYTICS_CODER_SYSTEM, ANALYTICS_CODER_VERSION
from db_schema import get_schema_context

from langchain_core.messages import HumanMessage, SystemMessage


def analytics_generate(state: dict) -> dict:
    """LLM phase: NL question (+ prior critique) -> SqlPlan."""
    question = state.get("user_input", "")
    system = ANALYTICS_CODER_SYSTEM.format(
        schema=get_schema_context(), reference_today=config.REFERENCE_TODAY)

    # Operator identity + recent conversation so "my/mine" and follow-ups resolve.
    preamble = []
    uid = state.get("current_user_id")
    if uid:
        preamble.append(f'The current operator\'s employee_id is "{uid}".')
    context = format_recent((state.get("messages") or [])[:-1], max_exchanges=5)
    if context:
        preamble.append(f"Recent conversation (for resolving follow-ups):\n{context}")
    human = ("\n\n".join(preamble) + "\n\n" if preamble else "") + f"Question: {question}"

    prior, review, result = state.get("sql_plan"), state.get("sql_review"), state.get("sql_result")
    db_error = result.get("error") if isinstance(result, dict) and not result.get("ok", True) else None
    if prior and (review or db_error):
        critique = "; ".join((review or {}).get("issues", [])) or (db_error or "")
        human += (f"\n\nYour previous SQL was rejected:\n{prior.get('sql')}\n"
                  f"Fix these problems and try again: {critique}")

    plan = get_reasoner().with_structured_output(SqlPlan).invoke(
        [SystemMessage(content=system), HumanMessage(content=human)])

    versions = dict(state.get("prompt_versions", {}))
    versions["analytics"] = ANALYTICS_CODER_VERSION
    return {
        "sql_plan": plan.model_dump(),
        "analytics_attempts": state.get("analytics_attempts", 0) + 1,
        "sql_review": None,   # clear prior verdict / result before re-review
        "sql_result": None,
        "prompt_versions": versions,
    }


async def analytics_execute(state: dict) -> dict:
    """Mechanical phase: run the approved SQL via run_readonly_query (no LLM)."""
    sql = state["sql_plan"]["sql"]
    tools = await mcp_client.get_all_tools()
    run_tool = next(t for t in mcp_client.tools_for("analytics", tools)
                    if t.name == "run_readonly_query")
    raw = await run_tool.ainvoke({"sql": sql})
    return {"sql_result": mcp_client.parse_tool_result(raw)}


# === SELF-TEST — full generate -> review -> execute loop.
# Needs GROQ + GOOGLE keys AND the HTTP services server running:
#     python mcp_server/server.py http        # (separate terminal)
#     python agents/nodes/analytics.py
# ============================================================================
if __name__ == "__main__":
    import asyncio
    from text_to_sql_reviewer import text_to_sql_reviewer_node

    async def _flow(question: str):
        state = {"user_input": question}
        for attempt in range(config.ANALYTICS_MAX_ATTEMPTS):
            state.update(analytics_generate(state))
            state.update(text_to_sql_reviewer_node(state))
            review = state["sql_review"]
            print(f"  attempt {attempt + 1}: approved={review['approved']} "
                  f"| sql: {state['sql_plan']['sql'][:90]}")
            if not review["approved"]:
                print(f"     issues: {review['issues']}")
                continue
            state.update(await analytics_execute(state))
            res = state["sql_result"]
            if res.get("ok"):
                print(f"  -> {res['row_count']} row(s): {res['rows'][:3]}")
                return
            print(f"  -> DB error, will retry: {res.get('error')}")
        print("  -> gave up after max attempts")

    for q in ["How many incidents are still open?",
              "Which machines are overdue for preventive service?"]:
        print(f"\nQ: {q}")
        asyncio.run(_flow(q))
