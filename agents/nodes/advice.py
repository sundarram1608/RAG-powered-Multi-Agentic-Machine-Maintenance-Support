"""
advice.py — Advice Agent node (general / preventive / how-to maintenance questions).

Triages the question in conversation context (AdvicePlan):
  - answer      : a general/preventive question -> retrieve the safety guide and let the
                  Output agent compose grounded guidance (advice mode). No machine/incident.
  - ask         : unclear if the user is facing this fault now -> ask one disambiguating
                  question (surfaced via the graph's interrupt).
  - troubleshoot: they've confirmed a live fault -> hand off to Intake/Diagnosis
                  (sets intent="troubleshoot" + symptom).

LLM: Groq Llama 3.3 70B (reasoner). Tool: safety_retrieval (answer path only — it is
machine-agnostic, so advice needs no machine id). Prompt: prompts/advice.py.
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


async def _safety(query: str) -> list:
    streaming.emit_tool("safety_retrieval", {"query": query})
    tools = await mcp_client.get_all_tools()
    tool = next(t for t in tools if t.name == "safety_retrieval")
    return mcp_client.parse_tool_result(await tool.ainvoke({"query": query, "k": 4}),
                                        expect_list=True)


async def advice_node(state: dict) -> dict:
    user_input = state.get("user_input", "")
    versions = dict(state.get("prompt_versions", {}))
    versions["advice"] = ADVICE_TRIAGE_VERSION

    # Context so short replies ("yes, on M05" / "no, just asking") are read correctly.
    context = history.format_recent((state.get("messages") or [])[:-1], max_exchanges=5)
    prior_q = state.get("clarification_question")
    human = ""
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

    # answer: ground in the (machine-agnostic) safety guide; Output composes the reply.
    safety = await _safety(topic)
    return {"advice_route": "answer", "retrieved_context": safety, "advice_topic": topic,
            "needs_clarification": False, "prompt_versions": versions}
