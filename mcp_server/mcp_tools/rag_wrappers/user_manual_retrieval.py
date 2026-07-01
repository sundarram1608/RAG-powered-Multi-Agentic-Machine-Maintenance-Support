"""
user_manual_retrieval — MCP wrapper over rag/retriever.py (user-manual search).

Thin adapter: it reuses the project's RAG retriever and flattens each chunk's
citation fields (source_file, page_start, page_end) to the top level so the
agent gets clean, low-noise passages to cite. Used by: Diagnosis, Guidance.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # .../mcp_tools
import _common  # noqa: F401  (side effect: puts rag/ on the import path)
from retriever import user_manual_retrieval as _retrieve_manual


def user_manual_retrieval(query: str, mvc_code: str, k: int = 5) -> list:
    """
    Retrieve the most relevant USER-MANUAL passages for a specific machine version
    — the agent's primary grounding for troubleshooting steps and how-to answers.
    Always resolve `mvc_code` first via get_machine; results are scoped to that one
    machine version, so passages never leak from a different model's manual.

    Args:
        query: The user's symptom or question in plain language
               (e.g. "bed not heating to target temperature").
        mvc_code: The machine version code from get_machine (e.g. "MVC02").
        k: Number of passages to return, most relevant first (default 5).

    Returns (most relevant first; smaller distance = closer match):
        [{text, source_file, page_start, page_end, distance}, ...]
        []   # nothing indexed for this mvc_code, or an empty query
    """
    if not (query or "").strip():
        return []
    chunks = _retrieve_manual(query, mvc_code, k=k)
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


# === SELF-TEST — python mcp_server/mcp_tools/rag_wrappers/user_manual_retrieval.py ===
if __name__ == "__main__":
    import json

    print(json.dumps(
        user_manual_retrieval("how do I level the bed?", "MVC01", k=3),
        indent=2, default=str,
    ))
