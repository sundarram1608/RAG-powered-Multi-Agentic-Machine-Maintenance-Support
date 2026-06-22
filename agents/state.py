"""
state.py — the shared graph State threaded through every agent node.

A single TypedDict (total=False, so each node writes only the keys it owns).
`messages` uses the add_messages reducer (append-only chat history). The rest is
typed scratch space the graph routes on — this structured state, NOT the raw
transcript, is the working memory (see agents/README.md → Memory & threads).
"""

from typing import Annotated, Optional, TypedDict

from langgraph.graph.message import add_messages


class State(TypedDict, total=False):
    # --- conversation & session ---
    messages: Annotated[list, add_messages]   # append-only chat history
    user_input: str                            # the current user turn (raw text)
    current_user_id: str                       # logged-in operator's employee_id

    # --- input guard ---
    input_safe: bool
    guard_reason: str

    # --- routing ---
    intent: str                                # troubleshoot | analytics | manage_incident | general

    # --- intake ---
    machine_id: Optional[str]
    mvc_code: Optional[str]
    machine_status: Optional[str]
    symptom: Optional[str]
    needs_clarification: bool
    clarification_question: Optional[str]

    # --- diagnosis ---
    retrieved_context: list                    # manual + safety chunks (cited)
    db_facts: dict                             # overdue / history / incidents / inventory
    diagnosis: Optional[dict]                  # the Diagnosis object (as dict)

    # --- verification ---
    verdict: Optional[dict]
    verify_attempts: int

    # --- decision & action ---
    decision_path: Optional[str]               # "self" | "technician"
    requires_approval: bool
    approval: Optional[str]                    # "approved" | "rejected"
    action_result: Optional[dict]

    # --- analytics (text-to-SQL coder <-> reviewer loop) ---
    sql_plan: Optional[dict]                   # SqlPlan (generated query)
    sql_review: Optional[dict]                 # SqlReview (reviewer verdict)
    sql_result: Optional[dict]                 # run_readonly_query result
    analytics_attempts: int                    # coder<->reviewer / DB-error retries
    sql_answer: Optional[dict]                 # final answer (rendered by Output)

    # --- output ---
    final_response: Optional[str]

    # --- observability ---
    prompt_versions: dict                      # node name -> prompt version used
