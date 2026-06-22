"""
retriever.py
------------
Retrieval phase of the RAG pipeline — two single-purpose functions the agent calls:

  user_manual_retrieval(query, mvc_code, k=5) -> manual chunks for that machine version
  safety_retrieval(query, k=2)                -> safety-guide chunks

Each embeds the query with the SAME BGE-M3 model used at ingestion, runs a
metadata-filtered cosine search over the persisted Chroma index, and returns the
top-k chunks as {text, metadata, distance} (smaller distance = more similar).

Generation (prompt assembly + the LLM call) is the agent's job — this module only
retrieves context. Whether the agent always calls safety_retrieval or only when a
query is safety-relevant is an agent-layer policy.
"""

from functools import lru_cache
from typing import List

from embedding_model_loader import get_embedding_model
from vectorstore import get_chroma_collection

MANUAL_K = 5
SAFETY_K = 2


@lru_cache(maxsize=1)
def _collection():
    """Cached handle to the persisted collection (opened once, reused per query)."""
    return get_chroma_collection()  # reset=False -> open the existing index


def _format(results) -> List[dict]:
    """Flatten a Chroma query result into a list of {text, metadata, distance}."""
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]
    return [
        {"text": text, "metadata": metadata, "distance": distance}
        for text, metadata, distance in zip(documents, metadatas, distances)
    ]


def user_manual_retrieval(query: str, mvc_code: str, k: int = MANUAL_K) -> List[dict]:
    """
    Retrieve the top-k user-manual chunks for a machine version.

    Args:
        query: the user's question / symptom (free text).
        mvc_code: the machine's version code (resolved by the agent from machine_id).
        k: number of chunks to return.
    """
    query_vector = get_embedding_model().embed_query(query)
    results = _collection().query(
        query_embeddings=[query_vector],
        n_results=k,
        where={"mvc_code": mvc_code},
    )
    return _format(results)


def safety_retrieval(query: str, k: int = SAFETY_K) -> List[dict]:
    """
    Retrieve the top-k safety-guide chunks. The agent calls this when the query is
    safety-relevant (hot surfaces, fumes/ventilation, electrical, etc.).
    """
    query_vector = get_embedding_model().embed_query(query)
    results = _collection().query(
        query_embeddings=[query_vector],
        n_results=k,
        where={"doc_type": "safety"},
    )
    return _format(results)


# =============================================================================
# SELF-TEST — NOT part of the module. Runs one manual query + one safety query
# against the live rag/chroma_store (requires the orchestrator to have run):
#     python rag/retriever.py
# =============================================================================
if __name__ == "__main__":
    def _show(results):
        for r in results:
            m = r["metadata"]
            print(f"  [{r['distance']:.4f}] {m.get('source_file')} "
                  f"p{m.get('page_start')}-{m.get('page_end')}")
            print(f"      {r['text'][:140].strip()}")

    print("--- user_manual_retrieval('how do I level the bed?', mvc_code='MVC01', k=3) ---")
    _show(user_manual_retrieval("how do I level the bed?", "MVC01", k=3))

    print("\n--- safety_retrieval('fumes and ventilation while printing') ---")
    _show(safety_retrieval("fumes and ventilation while printing"))
