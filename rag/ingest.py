"""
ingest.py
---------
Step 2 of the RAG ingestion pipeline: build the PDF -> metadata mapping.

For each machine version in `machine_versions` (the single source of truth), this
resolves its manual PDF and the tags to attach (mvc_code, model_name, doc_type).
The NIOSH safety guide isn't a machine version, so it's appended as a constant and
tagged mvc_code="ALL", doc_type="safety" (it applies to every machine).

If any PDF is missing (e.g. a fresh clone — the PDFs are git-ignored), this warns
and then triggers an automatic download via download_documents.py.

The orchestrator consumes get_document_mapping() and feeds each entry to the
loader (Step 1).
"""

import sys
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TABLES_DIR = PROJECT_ROOT / "synthetic_data" / "tables"
DOCS_DIR = PROJECT_ROOT / "synthetic_data" / "documents"
for _p in (str(TABLES_DIR), str(DOCS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from db_connection import get_connection      # reused project DB connection helper
import download_documents                      # reused PDF downloader

# The safety guide is not a machine version; it applies to ALL machines.
SAFETY_DOCUMENTS = [
    {
        "manual_path": "synthetic_data/documents/safety_guidelines/niosh_safe_3d_printing_2024-103.pdf",
        "base_metadata": {"mvc_code": "ALL", "model_name": "All Models", "doc_type": "safety"},
    },
]


@lru_cache(maxsize=1)
def get_document_mapping(conn=None) -> list:
    """
    Return the documents to ingest, each as:
        {"pdf_path": <absolute path>,
         "base_metadata": {"mvc_code", "model_name", "doc_type"}}

    Manuals are read from `machine_versions`; the safety guide is appended.
    Any missing PDF triggers a warning + automatic download.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT mvc_code, model_name, manual_path "
            "FROM machine_versions ORDER BY mvc_code;"
        )
        version_rows = cursor.fetchall()
        cursor.close()
    finally:
        if own_conn and conn.is_connected():
            conn.close()

    mapping = []
    for mvc_code, model_name, manual_path in version_rows:
        mapping.append({
            "pdf_path": str((PROJECT_ROOT / manual_path).resolve()),
            "base_metadata": {
                "mvc_code": mvc_code,
                "model_name": model_name,
                "doc_type": "user_manual",
            },
        })
    for safety in SAFETY_DOCUMENTS:
        mapping.append({
            "pdf_path": str((PROJECT_ROOT / safety["manual_path"]).resolve()),
            "base_metadata": safety["base_metadata"],
        })

    _ensure_documents_present(mapping)
    return mapping


def _ensure_documents_present(mapping) -> None:
    """Warn about any missing PDFs, then trigger an automatic download."""
    missing = [m["pdf_path"] for m in mapping if not Path(m["pdf_path"]).is_file()]
    if not missing:
        return

    print("⚠️  Missing document(s):")
    for path in missing:
        print(f"    - {path}")
    print("→ Attempting automatic download (download_documents.py)…\n")
    download_documents.main()  # idempotent: fetches missing, skips existing

    still_missing = [p for p in missing if not Path(p).is_file()]
    if still_missing:
        print("\n❌ Still missing after download "
              "(add its URL to download_documents.py):")
        for path in still_missing:
            print(f"    - {path}")


# =============================================================================
# SELF-TEST — NOT part of ingest. Print the resolved document mapping:
#     python rag/ingest.py
# =============================================================================
if __name__ == "__main__":
    documents = get_document_mapping()
    print(f"\nResolved {len(documents)} documents to ingest:\n")
    for entry in documents:
        meta = entry["base_metadata"]
        mark = "✓" if Path(entry["pdf_path"]).is_file() else "✗"
        print(f"  [{mark}] {meta['doc_type']:<12} mvc={meta['mvc_code']:<5} "
              f"{Path(entry['pdf_path']).name}")
