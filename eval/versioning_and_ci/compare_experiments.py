"""
compare_experiments.py — diff two eval runs (Phase 5e). DEV ONLY, zero tokens.

Either compare two named experiments, or a dataset's latest experiment vs its blessed
baseline. Prints a per-metric delta table (regressions marked). Complements LangSmith's
built-in side-by-side compare UI.

    python eval/versioning_and_ci/compare_experiments.py <exp_a> <exp_b>
    python eval/versioning_and_ci/compare_experiments.py --baseline fdm-routing
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ls_scores import latest_scores, scores_for_experiment

BASELINE = Path(__file__).resolve().parent / "baseline.json"


def _table(a_label, a, b_label, b):
    keys = sorted(set(a) | set(b))
    w = max((len(k) for k in keys), default=6)
    print(f"  {'metric':<{w}}  {a_label:>12}  {b_label:>12}  delta")
    for k in keys:
        av, bv = a.get(k), b.get(k)
        if av is None or bv is None:
            print(f"  {k:<{w}}  {str(av):>12}  {str(bv):>12}  —")
            continue
        d = bv - av
        flag = "  REGRESSION" if d < -0.05 else ("  +" if d > 0.05 else "")
        print(f"  {k:<{w}}  {av:>12.3f}  {bv:>12.3f}  {d:+.3f}{flag}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("experiments", nargs="*", help="two experiment names to compare")
    ap.add_argument("--baseline", help="dataset name: latest experiment vs blessed baseline")
    args = ap.parse_args()

    if args.baseline:
        cur, name, n = latest_scores(args.baseline)
        base = json.loads(BASELINE.read_text())["datasets"].get(args.baseline, {}).get("scores", {})
        print(f"{args.baseline}: baseline vs latest ('{name}', n={n})")
        _table("baseline", base, "latest", cur)
    elif len(args.experiments) == 2:
        a, b = args.experiments
        print(f"{a}  vs  {b}")
        _table(a[:12], scores_for_experiment(a), b[:12], scores_for_experiment(b))
    else:
        ap.error("give two experiment names, or --baseline <dataset>")


if __name__ == "__main__":
    main()
