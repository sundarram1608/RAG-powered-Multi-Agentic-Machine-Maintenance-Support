"""
derive_sql_expectations.py — verify each sql_case's gold answer against the live DB.

Runs every gold_sql (read-only) and checks that each string in
`expected_answer_contains` appears in the result. Keeps the analytics gold answers
honest (anchored to REFERENCE_TODAY = 2026-06-16). Prints a report; does not modify
files.

    python eval/build/derive_sql_expectations.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "synthetic_data" / "tables"))

SQL_FILE = ROOT / "eval" / "datasets" / "sql_cases.jsonl"


def main():
    from db_connection import get_connection
    c = get_connection(); cur = c.cursor()
    rows = [json.loads(l) for l in SQL_FILE.read_text().splitlines() if l.strip()]
    bad = 0
    for row in rows:
        ref = row["reference"]
        gold = ref.get("gold_sql")
        rid = row["id"]
        if not gold:
            print(f"  --   {rid}: (no gold_sql — PII/write trap)")
            continue
        try:
            cur.execute(gold)
            res = cur.fetchall()
        except Exception as e:
            print(f"  ✗   {rid}: gold_sql failed — {str(e)[:80]}")
            bad += 1
            continue
        flat = str(res)
        want = ref.get("expected_answer_contains", [])
        missing = [w for w in want if w not in flat]
        n = len(res)
        if missing:
            print(f"  ✗   {rid}: rows={n} missing {missing} | got {flat[:80]}")
            bad += 1
        else:
            tag = f"contains {want}" if want else f"{n} rows"
            print(f"  ok   {rid}: {tag}")
    c.close()
    print(f"\n{'ALL GOLD ANSWERS VERIFIED' if not bad else f'{bad} FAILED'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
