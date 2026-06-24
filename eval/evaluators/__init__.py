"""evaluators — graders per dataset (filename -> [evaluator fns])."""

from .deterministic import (
    gate,
    manage_plan,
    retrieval_metrics,
    routing_accuracy,
    safety_guard,
    sql_correctness,
)
from .llm_judges import answer_relevance, faithfulness

EVALUATORS = {
    "troubleshoot_cases.jsonl": [faithfulness, answer_relevance, gate],
    "retrieval_labels.jsonl": [retrieval_metrics],
    "sql_cases.jsonl": [sql_correctness],
    "routing_cases.jsonl": [routing_accuracy],
    "safety_redteam.jsonl": [safety_guard],
    "manage_cases.jsonl": [manage_plan],
}
