"""
ls_scores.py — read aggregate eval scores from LangSmith (no re-run, zero tokens).

The CI gate (Option A) reads the LATEST experiment per dataset and its per-metric
mean feedback. Shared by ci_gate.py and compare_experiments.py.
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env", override=False)

_client = None


def client():
    global _client
    if _client is None:
        from langsmith import Client
        _client = Client()
    return _client


def latest_experiment(dataset_name: str):
    """The most recent experiment (project) run against `dataset_name`, or None."""
    c = client()
    ds = c.read_dataset(dataset_name=dataset_name)
    projs = [p for p in c.list_projects(reference_dataset_id=ds.id) if getattr(p, "start_time", None)]
    return max(projs, key=lambda p: p.start_time) if projs else None


def _avgs_for_project(project_name: str):
    """metric_key -> mean score, aggregated across the experiment's root runs.
    (Project-level feedback_stats is often unpopulated, so we average run-level
    feedback.) Returns (scores, n_examples)."""
    c = client()
    runs = list(c.list_runs(project_name=project_name, is_root=True))
    agg = {}
    for r in runs:
        for key, st in (getattr(r, "feedback_stats", None) or {}).items():
            if isinstance(st, dict) and st.get("avg") is not None:
                agg.setdefault(key, []).append(st["avg"])
    return {k: sum(v) / len(v) for k, v in agg.items() if v}, len(runs)


def latest_scores(dataset_name: str):
    """(scores dict, experiment name, n_examples). Empty if no experiment found."""
    p = latest_experiment(dataset_name)
    if p is None:
        return {}, None, 0
    scores, n = _avgs_for_project(p.name)
    return scores, p.name, n


def scores_for_experiment(experiment_name: str):
    return _avgs_for_project(experiment_name)[0]


if __name__ == "__main__":
    import json
    for ds in ("fdm-routing", "fdm-sql", "fdm-retrieval", "fdm-troubleshoot",
               "fdm-safety", "fdm-manage"):
        sc, name, n = latest_scores(ds)
        print(f"{ds:16} {name} (n={n})")
        print("   ", json.dumps({k: round(v, 3) for k, v in sc.items()}))
