"""
advice.py — Advice Agent node (general / preventive / how-to maintenance questions).

Triages the question in conversation context (AdvicePlan):
  - answer      : a general/preventive question -> gather machine-agnostic grounding
                  (safety guide + EVERY model's manual) and let the Output agent compose
                  a shared answer + per-model deltas (advice mode). No machine/incident.
  - ask         : unclear if the user is facing this fault now -> ask one disambiguating
                  question (surfaced via the graph's interrupt).
  - troubleshoot: they've confirmed a live fault -> hand off to Intake/Diagnosis
                  (sets intent="troubleshoot" + symptom).

Advice stays machine-agnostic: instead of asking which machine, the answer path lists
the fleet's versions (list_machine_versions) and retrieves each model's manual, tagging
every chunk with its model so Output can write one shared answer and note the
model-specific differences — no machine id ever required.

LLM: Groq Llama 3.3 70B (reasoner). Tools (answer path): list_machine_versions,
user_manual_retrieval (per model), safety_retrieval. Prompt: prompts/advice.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agents/ on path
from utils import history
import mcp_client
from utils import streaming
from llms import get_reasoner
from schemas import AdvicePlan
from prompts.advice import ADVICE_TRIAGE_SYSTEM, ADVICE_TRIAGE_VERSION

from langchain_core.messages import HumanMessage, SystemMessage


async def _call(tool_name: str, args: dict) -> list:
    streaming.emit_tool(tool_name, args)
    tools = await mcp_client.get_all_tools()
    tool = next(t for t in tools if t.name == tool_name)
    return mcp_client.parse_tool_result(await tool.ainvoke(args), expect_list=True)


async def _grounding(query: str) -> list:
    """Machine-agnostic grounding for a how-to: the safety guide (all models) + each
    model's user manual, so Output can write a shared answer + per-model deltas. Every
    manual chunk is tagged with its model (model_name/mvc_code); safety chunks are
    tagged scope='safety (all models)' so Output can tell them apart."""
    safety = await _call("safety_retrieval", {"query": query, "k": 4})
    for chunk in safety:
        chunk["scope"] = "safety (all models)"
    context = list(safety)

    versions = await _call("list_machine_versions", {})   # [{mvc_code, model_name, ...}]
    for v in versions:
        mvc, model = v.get("mvc_code"), v.get("model_name")
        chunks = await _call("user_manual_retrieval",
                             {"query": query, "mvc_code": mvc, "k": 3})
        for chunk in chunks:
            chunk["mvc_code"], chunk["model_name"] = mvc, model
        context.extend(chunks)
    return context


async def advice_node(state: dict) -> dict:
    user_input = state.get("user_input", "")
    versions = dict(state.get("prompt_versions", {}))
    versions["advice"] = ADVICE_TRIAGE_VERSION

    # Context so short replies ("yes, on M05" / "no, just asking") are read correctly.
    context = history.format_recent((state.get("messages") or [])[:-1], max_exchanges=5)
    prior_q = state.get("clarification_question")
    human = ""
    if state.get("advice_general"):   # intake already confirmed this is a general question
        human += ("NOTE: it's already established the user is asking GENERALLY, not about a "
                  "machine they're operating now — do NOT ask whether they're facing it. If "
                  "they've named a topic, answer it; otherwise ask what they'd like to know.\n\n")
    if context:
        human += f"Recent conversation:\n{context}\n\n"
    if prior_q:
        human += f'You just asked the user: "{prior_q}"\n\n'
    human += f"User's latest message: {user_input}"

    plan = get_reasoner(structured=AdvicePlan).invoke(
        [SystemMessage(content=ADVICE_TRIAGE_SYSTEM), HumanMessage(content=human)])
    topic = plan.topic or user_input

    if plan.route == "ask":
        q = plan.question or ("Are you seeing this on a machine right now — I can diagnose "
                              "it — or are you asking for general guidance?")
        return {"advice_route": "ask", "needs_clarification": True,
                "clarification_question": q, "prompt_versions": versions}

    if plan.route == "troubleshoot":
        # Hand off: the user is facing it now -> Intake/Diagnosis (which will get the machine).
        return {"advice_route": "troubleshoot", "intent": "troubleshoot", "symptom": topic,
                "needs_clarification": False, "prompt_versions": versions}

    # answer: gather machine-agnostic grounding (safety + every model's manual, tagged);
    # Output composes one shared answer + per-model deltas.
    grounding = await _grounding(topic)
    return {"advice_route": "answer", "retrieved_context": grounding, "advice_topic": topic,
            "needs_clarification": False, "prompt_versions": versions}
