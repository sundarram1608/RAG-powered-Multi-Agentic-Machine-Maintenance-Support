"""
text_to_sql_reviewer.py — Text-to-SQL Reviewer node.

Judges the Analytics coder's SQL BEFORE it runs: grounded (real schema), relevant
(answers the question), safe (read-only, single statement, no phone). No tools.
A semantic check that complements run_readonly_query's mechanical validation and
the read-only DB user (defense in depth). On reject, the graph loops back to the
Analytics coder with the issues (capped at ANALYTICS_MAX_ATTEMPTS).

LLM: Gemini 2.5 Flash-Lite (independent judge — a different model family than the coder).
Prompt: prompts/text_to_sql_reviewer.py (versioned).
Input  (reads state): user_input, sql_plan.
Output (writes state): sql_review (SqlReview dict), prompt_versions["text_to_sql_reviewer"].
Structured output: Pydantic `SqlReview` via with_structured_output.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agents/ on path
import config
from llms import get_judge_structured
from schemas import SqlReview
from prompts.text_to_sql_reviewer import (
    TEXT_TO_SQL_REVIEWER_SYSTEM,
    TEXT_TO_SQL_REVIEWER_VERSION,
)
from db_schema import get_schema_context

from langchain_core.messages import HumanMessage, SystemMessage


def text_to_sql_reviewer_node(state: dict) -> dict:
    """Judge state['sql_plan']; return {sql_review, prompt_versions}."""
    question = state.get("user_input", "")
    plan = state["sql_plan"]
    system = TEXT_TO_SQL_REVIEWER_SYSTEM.format(
        schema=get_schema_context(), reference_today=config.REFERENCE_TODAY)
    human = f"User question: {question}\n\nProposed SQL:\n{plan['sql']}"

    review = get_judge_structured(SqlReview).invoke(
        [SystemMessage(content=system), HumanMessage(content=human)])

    versions = dict(state.get("prompt_versions", {}))
    versions["text_to_sql_reviewer"] = TEXT_TO_SQL_REVIEWER_VERSION
    return {"sql_review": review.model_dump(), "prompt_versions": versions}


# === SELF-TEST — python agents/nodes/text_to_sql_reviewer.py  (needs GOOGLE key) ===
if __name__ == "__main__":
    cases = [
        ("good", "How many incidents are open?",
         "SELECT COUNT(*) AS open_count FROM incidents WHERE incident_closure_date IS NULL"),
        ("ungrounded (bad table)", "How many incidents are open?",
         "SELECT COUNT(*) FROM tickets WHERE status = 'open'"),
        ("unsafe (phone)", "list staff phone numbers",
         "SELECT full_name, phone FROM employees"),
    ]
    print(f"prompt_version = {TEXT_TO_SQL_REVIEWER_VERSION}\n")
    for label, q, sql in cases:
        out = text_to_sql_reviewer_node(
            {"user_input": q, "sql_plan": {"sql": sql}})
        r = out["sql_review"]
        print(f"{label:24} approved={str(r['approved']):5} "
              f"grounded={str(r['grounded']):5} relevant={str(r['relevant']):5} "
              f"safe={str(r['safe']):5} issues={r['issues'][:1]}")
