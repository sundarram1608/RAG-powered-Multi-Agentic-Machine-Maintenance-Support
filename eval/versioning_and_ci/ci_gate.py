"""
ci_gate.py — regression gate (Phase 5e). DEV/CI ONLY — never runs in the live agent.

Reads the LATEST experiment scores per dataset from LangSmith (Option A — no re-run,
zero tokens) and compares the BLOCKING metrics against the blessed baseline.json
(± TOLERANCE). Exits non-zero on a regression so a PR/merge can be blocked.
Diagnosis-faithfulness/answer-relevance are ADVISORY (printed, never block) because
the free judge is flaky. safety/manage are added once their clean baseline exists.

    python eval/versioning_and_ci/ci_gate.py            # gate vs baseline (exit 0/1)
    python eval/versioning_and_ci/ci_gate.py --bless    # (re)write baseline.json from latest valid runs
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ls_scores import latest_scores
from version_manifest import get_manifest

BASELINE = Path(__file__).resolve().parent / "baseline.json"
TOLERANCE = 0.05   # a blocking metric may dip this far below baseline before it FAILs

# dataset -> blocking (block CI on regression) + advisory (report only)
GATE = {
    "fdm-routing":      {"blocking": ["intent_correct"], "advisory": []},
    "fdm-sql":          {"blocking": ["sql_rows_match", "sql_readonly", "sql_no_phone"], "advisory": []},
    "fdm-retrieval":    {"blocking": ["recall@k"], "advisory": ["precision@k", "mrr", "ndcg"]},
    "fdm-troubleshoot": {"blocking": ["needs_technician_correct"],
                         "advisory": ["faithfulness", "answer_relevance"]},
    # added after a clean (non-quota-capped) re-run blesses their baseline:
    # "fdm-safety": {"blocking": ["guard_correct", "no_pii_leak"], "advisory": []},
    # "fdm-manage": {"blocking": ["manage_action_correct"], "advisory": []},
}
# only datasets with VALID baselines today (safety/manage deferred — tainted by Groq cap)
VALID_FOR_BASELINE = {"fdm-routing", "fdm-sql", "fdm-retrieval", "fdm-troubleshoot"}


def bless():
    data = {"blessed_at": datetime.now().isoformat(timespec="seconds"),
            "tolerance": TOLERANCE, "manifest": get_manifest(), "datasets": {}}
    for ds, cfg in GATE.items():
        if ds not in VALID_FOR_BASELINE:
            continue
        scores, name, n = latest_scores(ds)
        data["datasets"][ds] = {"experiment": name, "n": n,
                                "scores": {m: scores.get(m) for m in cfg["blocking"]}}
        print(f"  blessed {ds}: {data['datasets'][ds]['scores']} (from {name})")
    BASELINE.write_text(json.dumps(data, indent=2) + "\n")
    print(f"\nWrote {BASELINE}")


def gate():
    if not BASELINE.exists():
        print("No baseline.json — run with --bless first."); sys.exit(2)
    base = json.loads(BASELINE.read_text())
    failures = []
    for ds, cfg in GATE.items():
        scores, name, n = latest_scores(ds)
        bscores = (base["datasets"].get(ds) or {}).get("scores", {})
        print(f"\n[{ds}] latest='{name}' (n={n})")
        for m in cfg["blocking"]:
            cur, bl = scores.get(m), bscores.get(m)
            if bl is None:
                print(f"  – {m}: no baseline yet (not blessed) — skipped")
            elif cur is None:
                print(f"  ! {m}: no current score — can't gate (re-run the eval)")
            elif cur >= bl - TOLERANCE:
                print(f"  ✓ {m}: {cur:.3f} vs baseline {bl:.3f}")
            else:
                print(f"  ✗ {m}: {cur:.3f} vs baseline {bl:.3f}  REGRESSION")
                failures.append(f"{ds}/{m}: {cur:.3f} < {bl:.3f} − {TOLERANCE}")
        for m in cfg["advisory"]:
            cur = scores.get(m)
            print(f"  · {m}: {'n/a' if cur is None else round(cur, 3)} (advisory)")

    if failures:
        print("\nGATE: FAIL"); [print("   -", f) for f in failures]; sys.exit(1)
    print("\nGATE: PASS"); sys.exit(0)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bless", action="store_true", help="(re)write baseline.json from latest valid runs")
    if ap.parse_args().bless:
        bless()
    else:
        gate()
