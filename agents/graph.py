"""
graph.py — Phase 4c: assemble the 12 agents (14 nodes) into one LangGraph workflow.

Topology (see agents/README.md → Graph assembly):
  START -> input -(safe)-> supervisor -(intent)->
     general          -> output
     analytics        -> analytics_generate -> text_to_sql_reviewer -> analytics_execute
                         (reviewer-reject / db-error loop back to analytics_generate, cap 3)
     manage_incident  -> manage_resolve [interrupts] -> manage_execute -> output
     troubleshoot     -> intake [interrupt] -> diagnosis -> verifier
                         verifier: approved+needs_technician -> technician_action
                                   approved+!needs_technician -> decider [interrupt]
                                   reject(<3) -> diagnosis ; reject(exhausted) -> technician_action
                         decider: self -> self_action [interrupt] ; technician -> technician_action
                         self_action: complete -> output ; book_technician -> technician_action
  everything -> output -> END

Interrupts use LangGraph interrupt() inside thin wrappers around the (unchanged,
standalone-tested) node functions. Checkpointer = MemorySaver (dev).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # agents/ on path
import clarify
import config
from state import State

from nodes.input import input_node
from nodes.supervisor import supervisor_node
from nodes.analytics import analytics_generate, analytics_execute
from nodes.text_to_sql_reviewer import text_to_sql_reviewer_node
from nodes.manage_incident import manage_resolve, manage_execute
from nodes.advice import advice_node
from nodes.intake import intake_node
from nodes.diagnosis import diagnosis_node
from nodes.verifier import verifier_node
from nodes.decider import decider_node, decider_question
from nodes.self_action import self_action_execute, self_action_message
from nodes.technician_action import technician_action
from nodes.output import output_node

from langgraph.graph import START, END, StateGraph
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt

MAX_CLARIFY = 4   # cap on clarification re-asks per interrupting node


# ── interrupt wrappers (human-in-the-loop) — reuse the plain node fns ──

async def intake_interrupt(state: dict) -> dict:
    working, update = dict(state), {}
    for _ in range(MAX_CLARIFY):
        update = await intake_node(working)
        working.update(update)
        if not update.get("needs_clarification") or update.get("clarify_abandoned"):
            return update              # resolved, or the LLM judged user_quit -> stop
        # No is_bail shortcut here: intake_node's LLM decides intent in context, so an
        # acknowledgement ("ok"/"got it") CONTINUES (re-ask) and only an explicit
        # cancel / topic-switch (user_quit) stops.
        working["user_input"] = interrupt(
            {"type": "clarify", "question": working.get("clarification_question")})
    # Re-ask cap hit and info still missing -> stop cleanly (route to output).
    field = "machine_id" if not working.get("machine_id") else "symptom"
    return {**update, "needs_clarification": False, "clarify_abandoned": True,
            "final_response": clarify.give_up(field)}


async def advice_interrupt(state: dict) -> dict:
    working, update = dict(state), {}
    for _ in range(MAX_CLARIFY):
        update = await advice_node(working)
        working.update(update)
        if not update.get("needs_clarification"):
            return update              # answer (-> output) or troubleshoot handoff (-> intake)
        working["user_input"] = interrupt(
            {"type": "clarify", "question": working.get("clarification_question")})
    # Cap hit while still disambiguating -> just answer generally rather than loop.
    return {**update, "advice_route": "answer", "needs_clarification": False}


async def decider_interrupt(state: dict) -> dict:
    question = decider_question(state.get("diagnosis", {}))
    update = {}
    for _ in range(MAX_CLARIFY):
        reply = interrupt({"type": "decision", "question": question,
                           "options": ["self", "technician"]})
        update = decider_node({**state, "user_input": reply})
        if not update.get("needs_clarification"):
            return update
        question = update.get("clarification_question") or question
    return update


async def self_action_interrupt(state: dict) -> dict:
    choice = interrupt({"type": "choice",
                        "guidance": self_action_message(state.get("diagnosis", {})),
                        "options": ["complete", "technician"]})
    return await self_action_execute({**state, "self_action_choice": choice})


async def manage_resolve_interrupt(state: dict) -> dict:
    working, update = dict(state), {}
    for _ in range(MAX_CLARIFY + 2):
        update = await manage_resolve(working)
        working.update(update)
        if update.get("needs_clarification"):
            reply = interrupt(
                {"type": "clarify", "question": working.get("clarification_question")})
            if clarify.is_bail(reply):     # "ok" / "cancel" / "never mind" -> stop cleanly
                plan = {**(working.get("manage_plan") or {}), "action": "cancelled"}
                return {**update, "manage_plan": plan, "action_result": {"action": "cancelled"},
                        "clarify_abandoned": True, "final_response": clarify.bailed()}
            working["user_input"] = reply
            continue
        if working.get("requires_approval"):
            decision = interrupt({"type": "approve",
                                  "summary": (working.get("manage_plan") or {}).get("plan_summary"),
                                  "options": ["approve", "reject"]})
            if str(decision).lower().startswith("rej"):
                plan = {**(working.get("manage_plan") or {}), "action": "cancelled"}
                return {**update, "manage_plan": plan, "action_result": {"action": "cancelled"}}
            return update   # approved -> manage_execute via the edge
        return update       # unsupported / done
    # Re-ask cap hit and still unresolved -> stop cleanly (unsupported routes to output).
    act = (working.get("manage_plan") or {}).get("action")
    field = "comment" if act in ("close", "update_comment") else "incident_id"
    plan = {**(working.get("manage_plan") or {}), "action": "unsupported",
            "plan_summary": clarify.give_up(field)}
    return {**update, "manage_plan": plan}


# ── conditional-edge routers (state -> next node name) ──

def route_after_input(state):
    return "output" if state.get("input_safe") is False else "supervisor"


def route_after_supervisor(state):
    return {"troubleshoot": "intake", "advice": "advice", "analytics": "analytics_generate",
            "manage_incident": "manage_resolve", "general": "output"
            }.get(state.get("intent"), "output")


def route_after_advice(state):
    # handoff to troubleshooting (user is facing it now) -> intake; else answer -> output.
    return "intake" if state.get("advice_route") == "troubleshoot" else "output"


def route_after_reviewer(state):
    if (state.get("sql_review") or {}).get("approved"):
        return "analytics_execute"
    return ("analytics_generate"
            if state.get("analytics_attempts", 0) < config.ANALYTICS_MAX_ATTEMPTS else "output")


def route_after_analytics_execute(state):
    if (state.get("sql_result") or {}).get("ok"):
        return "output"
    return ("analytics_generate"
            if state.get("analytics_attempts", 0) < config.ANALYTICS_MAX_ATTEMPTS else "output")


def route_after_manage_resolve(state):
    action = (state.get("manage_plan") or {}).get("action")
    return "output" if action in ("unsupported", "cancelled") else "manage_execute"


def route_after_intake(state):
    # clarify_abandoned (re-ask cap hit) -> output with the give-up message; else diagnose.
    return "output" if state.get("clarify_abandoned") else "diagnosis"


def route_after_verifier(state):
    verdict = state.get("verdict") or {}
    if verdict.get("approved"):
        return "technician_action" if (state.get("diagnosis") or {}).get("needs_technician") else "decider"
    if state.get("verify_attempts", 0) < config.VERIFY_MAX_ATTEMPTS:
        return "diagnosis"
    return "technician_action"   # exhausted -> dispatch (verifier_exhausted already set)


def route_after_decider(state):
    return "self_action" if state.get("decision_path") == "self" else "technician_action"


def route_after_self_action(state):
    action = (state.get("action_result") or {}).get("action")
    return "technician_action" if action == "escalate_to_technician" else "output"


# ── build & compile ──

def build_graph() -> StateGraph:
    b = StateGraph(State)
    b.add_node("input", input_node)
    b.add_node("supervisor", supervisor_node)
    b.add_node("analytics_generate", analytics_generate)
    b.add_node("text_to_sql_reviewer", text_to_sql_reviewer_node)
    b.add_node("analytics_execute", analytics_execute)
    b.add_node("manage_resolve", manage_resolve_interrupt)
    b.add_node("manage_execute", manage_execute)
    b.add_node("advice", advice_interrupt)
    b.add_node("intake", intake_interrupt)
    b.add_node("diagnosis", diagnosis_node)
    b.add_node("verifier", verifier_node)
    b.add_node("decider", decider_interrupt)
    b.add_node("self_action", self_action_interrupt)
    b.add_node("technician_action", technician_action)
    b.add_node("output", output_node)

    b.add_edge(START, "input")
    b.add_conditional_edges("input", route_after_input, ["output", "supervisor"])
    b.add_conditional_edges("supervisor", route_after_supervisor,
                            ["intake", "advice", "analytics_generate", "manage_resolve", "output"])
    b.add_conditional_edges("advice", route_after_advice, ["intake", "output"])
    b.add_edge("analytics_generate", "text_to_sql_reviewer")
    b.add_conditional_edges("text_to_sql_reviewer", route_after_reviewer,
                            ["analytics_execute", "analytics_generate", "output"])
    b.add_conditional_edges("analytics_execute", route_after_analytics_execute,
                            ["output", "analytics_generate"])
    b.add_conditional_edges("manage_resolve", route_after_manage_resolve,
                            ["manage_execute", "output"])
    b.add_edge("manage_execute", "output")
    b.add_conditional_edges("intake", route_after_intake, ["diagnosis", "output"])
    b.add_edge("diagnosis", "verifier")
    b.add_conditional_edges("verifier", route_after_verifier,
                            ["technician_action", "decider", "diagnosis"])
    b.add_conditional_edges("decider", route_after_decider,
                            ["self_action", "technician_action"])
    b.add_conditional_edges("self_action", route_after_self_action,
                            ["technician_action", "output"])
    b.add_edge("technician_action", "output")
    b.add_edge("output", END)
    return b


app_graph = build_graph().compile(checkpointer=MemorySaver())


# === SMOKE — python agents/graph.py  (compiles + prints the mermaid diagram) ===
if __name__ == "__main__":
    g = app_graph.get_graph()
    print(f"Compiled: {len(g.nodes)} nodes, {len(g.edges)} edges\n")
    print(g.draw_mermaid())
