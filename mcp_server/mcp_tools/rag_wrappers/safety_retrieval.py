"""
safety_retrieval — MCP wrapper over rag/retriever.py (safety-guide search).

Thin adapter: it reuses the project's RAG retriever and flattens each chunk's
citation fields to the top level. The safety guide applies to all models, so no
machine version filter is needed. Used by: Diagnosis and the Advice agent (Self
Action does not call this — it re-uses the safety context Diagnosis retrieved).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # .../mcp_tools
import _common  # noqa: F401  (side effect: puts rag/ on the import path)
from retriever import safety_retrieval as _retrieve_safety


def safety_retrieval(query: str, k: int = 2) -> list:
    """
    Retrieve relevant SAFETY-GUIDE passages (hot surfaces, fumes/ventilation,
    moving parts, electrical hazards, etc.) so a recommended fix carries the right
    precautions. Not machine-specific — the safety guide applies to all models, so
    no machine version is needed. Call this whenever a fix involves physically
    handling any hazard on the machine.

    Args:
        query: The hazard or task in plain language
               (e.g. "handling the heated bed", "ventilation while printing").
        k: Number of passages to return, most relevant first (default 2).

    Returns (most relevant first; smaller distance = closer match):
        [{text, source_file, page_start, page_end, distance}, ...]
        []   # nothing indexed, or an empty query
    """
    if not (query or "").strip():
        return []
    chunks = _retrieve_safety(query, k=k)
    return [
        {
            "text": _common.clean_chunk_text(c["text"]),
            "source_file": c["metadata"].get("source_file"),
            "page_start": c["metadata"].get("page_start"),
            "page_end": c["metadata"].get("page_end"),
            "distance": c["distance"],
        }
        for c in chunks
    ]


# === SELF-TEST — python mcp_server/mcp_tools/rag_wrappers/safety_retrieval.py ===
if __name__ == "__main__":
    import json

    print(json.dumps(
        safety_retrieval("fumes and ventilation while printing"),
        indent=2, default=str,
    ))
