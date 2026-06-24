"""
llm_judges.py — LLM-as-judge graders via openevals, bound to the eval judge
(OpenRouter / DeepSeek). Faithfulness (groundedness) + answer relevance for the
troubleshoot diagnosis. Each returns {"key","score" (0-1),"comment"}; skipped cases
return score=None.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # eval/ -> eval_llm
from eval_llm import get_eval_judge

from openevals.llm import create_llm_as_judge
from openevals.prompts import ANSWER_RELEVANCE_PROMPT, RAG_GROUNDEDNESS_PROMPT

_faith = None
_rel = None


def _faith_judge():
    global _faith
    if _faith is None:
        _faith = create_llm_as_judge(prompt=RAG_GROUNDEDNESS_PROMPT, judge=get_eval_judge(),
                                     feedback_key="faithfulness", continuous=True)
    return _faith


def _rel_judge():
    global _rel
    if _rel is None:
        _rel = create_llm_as_judge(prompt=ANSWER_RELEVANCE_PROMPT, judge=get_eval_judge(),
                                   feedback_key="answer_relevance", continuous=True)
    return _rel


def faithfulness(run, example):
    ref, out = example.outputs or {}, run.outputs or {}
    if ref.get("expect_low_confidence"):
        return {"key": "faithfulness", "score": None, "comment": "skipped (out-of-manual)"}
    try:
        r = _faith_judge()(context=out.get("context_text", ""), outputs=out.get("diagnosis_text", ""))
        return {"key": "faithfulness", "score": r.get("score"), "comment": (r.get("comment") or "")[:300]}
    except Exception as e:
        return {"key": "faithfulness", "score": None, "comment": f"judge error: {e}"[:200]}


def answer_relevance(run, example):
    ref, out = example.outputs or {}, run.outputs or {}
    if ref.get("expect_low_confidence"):
        return {"key": "answer_relevance", "score": None, "comment": "skipped (out-of-manual)"}
    try:
        r = _rel_judge()(inputs=(example.inputs or {}).get("symptom", ""),
                         outputs=out.get("diagnosis_text", ""))
        return {"key": "answer_relevance", "score": r.get("score"), "comment": (r.get("comment") or "")[:300]}
    except Exception as e:
        return {"key": "answer_relevance", "score": None, "comment": f"judge error: {e}"[:200]}
