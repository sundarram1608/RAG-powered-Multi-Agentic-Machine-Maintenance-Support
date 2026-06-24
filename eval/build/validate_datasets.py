"""
validate_datasets.py — lint every golden dataset before use.

Checks, per row:
  - schema (Pydantic, from datasets/schemas.py),
  - referential: machine_id exists + mvc_code matches the DB; cited/relevant
    source_file is known and page ranges are sane + within the document's page count
    (from the Chroma metadata); routing intent + manage action enums; gold_sql is a
    single read-only SELECT (via mcp_server/safety.validate_select_sql).

    python eval/build/validate_datasets.py        # exits non-zero on any failure
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval" / "datasets"))
sys.path.insert(0, str(ROOT / "synthetic_data" / "tables"))
sys.path.insert(0, str(ROOT / "mcp_server"))

import schemas as S
from schemas import ALLOWED_SOURCES, DATASETS, INTENTS, MANAGE_ACTIONS

DATA_DIR = ROOT / "eval" / "datasets"


def _machine_map():
    from db_connection import get_connection
    c = get_connection(); cur = c.cursor()
    cur.execute("SELECT machine_id, mvc_code FROM machines")
    m = {mid: mvc for mid, mvc in cur.fetchall()}
    c.close()
    return m


def _max_pages():
    import chromadb
    col = chromadb.PersistentClient(path=str(ROOT / "rag" / "chroma_store")).get_collection("maintenance_manuals")
    g = col.get(include=["metadatas"])
    mx = {}
    for meta in g["metadatas"]:
        sf = meta["source_file"]
        mx[sf] = max(mx.get(sf, 0), int(meta["page_end"]))
    return mx


def _readonly_ok(sql):
    try:
        from safety import validate_select_sql
        validate_select_sql(sql)
        return True, ""
    except Exception as e:
        return False, str(e)[:80]


def main():
    machines = _machine_map()
    maxpages = _max_pages()
    total, fails = 0, 0

    for fname, (model, _) in DATASETS.items():
        path = DATA_DIR / fname
        rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        issues = []
        for row in rows:
            total += 1
            rid = row.get("id", "?")
            try:
                ex = model(**row)
            except Exception as e:
                issues.append(f"{rid}: schema — {str(e)[:100]}")
                continue
            ref, inp = row["reference"], row["inputs"]

            # machine/mvc consistency
            mid = inp.get("machine_id")
            if mid:
                if mid not in machines:
                    issues.append(f"{rid}: machine {mid} not in DB")
                elif inp.get("mvc_code") and machines[mid] != inp["mvc_code"]:
                    issues.append(f"{rid}: {mid} is {machines[mid]}, not {inp['mvc_code']}")

            # page refs (cited_pages / relevant)
            for pr in (ref.get("cited_pages", []) + ref.get("relevant", [])):
                sf = pr["source_file"]
                if sf not in ALLOWED_SOURCES:
                    issues.append(f"{rid}: unknown source_file {sf}")
                elif not (0 < pr["page_start"] <= pr["page_end"] <= maxpages.get(sf, 10**9)):
                    issues.append(f"{rid}: bad page range {pr['page_start']}-{pr['page_end']} for {sf} (max {maxpages.get(sf)})")

            # enums
            if fname == "routing_cases.jsonl" and ref.get("intent") not in INTENTS:
                issues.append(f"{rid}: bad intent {ref.get('intent')}")
            if fname == "manage_cases.jsonl" and ref.get("action") and ref["action"] not in MANAGE_ACTIONS:
                issues.append(f"{rid}: bad action {ref.get('action')}")

            # gold_sql read-only
            if fname == "sql_cases.jsonl" and ref.get("gold_sql"):
                ok, why = _readonly_ok(ref["gold_sql"])
                if not ok:
                    issues.append(f"{rid}: gold_sql not read-only/valid — {why}")

        fails += len(issues)
        print(f"\n[{fname}] {len(rows)} rows — {'OK' if not issues else f'{len(issues)} ISSUES'}")
        for i in issues:
            print(f"    ✗ {i}")

    print(f"\n{'ALL VALID' if not fails else f'{fails} ISSUES'} across {total} examples")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
