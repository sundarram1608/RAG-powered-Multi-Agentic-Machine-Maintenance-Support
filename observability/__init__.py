"""
observability — Phase 5a tracing layer (LangSmith).

Public API (used by agents/api.py):
    make_config(thread_id, user_id, message, *, turn_id, run_name, base) -> (config, run_id, metadata)
    enrich_run(run_id, base_metadata, state) -> None
    new_turn_id() -> str
    tracing_on() -> bool
    get_client() -> langsmith.Client (PII-masking)
"""

from .governance import flag_for_review, log_feedback, review_reason
from .tracing import (
    PROJECT,
    enrich_run,
    get_client,
    make_config,
    new_turn_id,
    tracing_on,
)

__all__ = [
    "PROJECT",
    "enrich_run",
    "flag_for_review",
    "get_client",
    "log_feedback",
    "make_config",
    "new_turn_id",
    "review_reason",
    "tracing_on",
]
