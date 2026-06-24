"""
governance.py — Phase 5f free hooks (LangSmith): human feedback + review-queue flagging.

Two hooks, both no-ops unless LANGSMITH_TRACING=true:
  log_feedback(run_id, score, comment)  — record a thumbs up/down on a turn's trace
                                          (called by the Phase 6 UI; needs the run_id
                                          that api.start_turn/resume_turn now returns).
  flag_for_review(run_id, reason)       — add a run to a LangSmith annotation queue for
                                          human review. review_reason(state) decides which
                                          runs qualify; enrich_run() calls it per turn.

Governance only — it observes production runs and routes the risky ones to a human; it
never changes the live agent's behaviour. Annotation queues are free-tier.
"""

import os

from .tracing import get_client, tracing_on

REVIEW_QUEUE = os.getenv("LANGSMITH_REVIEW_QUEUE", "fdm-review")
LOW_SCORE = 2   # Verdict.score is 1-5 (1=poor); <= this -> route to human review

_queue_id = None


def _queue_id_cached():
    """Create-or-get the annotation queue; cache its id (None on failure)."""
    global _queue_id
    if _queue_id is None:
        try:
            c = get_client()
            existing = list(c.list_annotation_queues(name=REVIEW_QUEUE, limit=1))
            _queue_id = (existing[0].id if existing
                         else c.create_annotation_queue(
                             name=REVIEW_QUEUE,
                             description="FDM agent runs flagged for human review "
                                         "(low confidence, exhausted, escalations, DB writes).").id)
        except Exception:
            _queue_id = None
    return _queue_id


def log_feedback(run_id, score, comment=None, key="user_feedback") -> bool:
    """Record human feedback on a turn (e.g. UI thumbs up/down). score: 1=👍, 0=👎."""
    if not tracing_on() or not run_id:
        return False
    try:
        get_client().create_feedback(run_id, key=key, score=score, comment=comment)
        return True
    except Exception:
        return False


def review_reason(state) -> str | None:
    """Why this run should go to human review, or None. (Criteria are tunable.)"""
    verdict = state.get("verdict") or {}
    action = (state.get("action_result") or {}).get("action")
    score = verdict.get("score")
    if state.get("verifier_exhausted"):
        return "verifier_exhausted"
    if score is not None and score <= LOW_SCORE:
        return f"low_verdict_score={score}"
    if (state.get("action_result") or {}).get("escalated"):
        return "supervisor_escalation"
    if action in ("close", "assign", "update_comment"):
        return f"manage_write:{action}"
    return None


def flag_for_review(run_id, reason) -> bool:
    """Add a run to the annotation queue (+ tag it with the reason). No-op if untraced."""
    if not tracing_on() or not run_id or not reason:
        return False
    try:
        c = get_client()
        qid = _queue_id_cached()
        if not qid:
            return False
        c.add_runs_to_annotation_queue(qid, run_ids=[str(run_id)])
        c.create_feedback(run_id, key="flagged_for_review", value=reason, comment=reason)
        return True
    except Exception:
        return False
