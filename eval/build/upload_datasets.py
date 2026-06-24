"""
upload_datasets.py — push the local golden datasets to LangSmith (idempotent).

Local JSONL is the source of truth; this mirrors each file to a LangSmith dataset
(`outputs` = the reference) so 5c's evaluate() + the experiment UI can use them.
Idempotent: an existing dataset of the same name is replaced (clean version).

    python eval/build/upload_datasets.py --dry-run   # print what would upload
    python eval/build/upload_datasets.py             # actually upload (needs LANGSMITH_API_KEY)
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval" / "datasets"))
from schemas import DATASETS

DATA_DIR = ROOT / "eval" / "datasets"
DRY = "--dry-run" in sys.argv


def _load(fname):
    return [json.loads(l) for l in (DATA_DIR / fname).read_text().splitlines() if l.strip()]


def main():
    if DRY:
        for fname, (_, ds_name) in DATASETS.items():
            print(f"  [{ds_name}] would upload {len(_load(fname))} examples from {fname}")
        print("\n(dry run — nothing sent)")
        return

    from langsmith import Client
    client = Client()

    for fname, (_, ds_name) in DATASETS.items():
        rows = _load(fname)
        if client.has_dataset(dataset_name=ds_name):
            client.delete_dataset(dataset_name=ds_name)   # replace -> clean version
        ds = client.create_dataset(
            dataset_name=ds_name,
            description=f"FDM agentic eval — {fname} ({len(rows)} examples)")
        client.create_examples(
            dataset_id=ds.id,
            inputs=[r["inputs"] for r in rows],
            outputs=[r["reference"] for r in rows],
            metadata=[{**r.get("metadata", {}), "case_id": r["id"]} for r in rows])
        print(f"  [{ds_name}] uploaded {len(rows)} examples")
    print("\nDone. Open the datasets in LangSmith to review.")


if __name__ == "__main__":
    main()
