"""
retriever.py
------------
Retrieval phase of the RAG pipeline — two single-purpose functions the agent calls:

  user_manual_retrieval(query, mvc_code, k=5) -> manual chunks for that machine version
  safety_retrieval(query, k=2)                -> safety-guide chunks

Each embeds the query with the SAME BGE-M3 model used at ingestion, runs a
metadata-filtered cosine search over the persisted Chroma index to fetch a wider
CANDIDATE set, then RERANKS those candidates with the BGE cross-encoder
(bge-reranker-v2-m3) and returns the top-k as
{text, metadata, distance, rerank_score} (higher rerank_score = more relevant;
distance is the original cosine distance, kept for reference).

Reranking is query-time only — it reorders candidates by re-scoring the chunk
TEXT against the query. It does NOT change the stored embeddings or the index.
(Systematic RAG evaluation — context precision/recall, faithfulness — lands in
Phase 5 with LangSmith.)

Generation (prompt assembly + the LLM call) is the agent's job — this module only
retrieves context. Whether the agent always calls safety_retrieval or only when a
query is safety-relevant is an agent-layer policy.
"""

from functools import lru_cache
from typing import List

from embedding_model_loader import get_embedding_model
from reranker_loader import get_reranker
from vectorstore import get_chroma_collection

MANUAL_K = 5
SAFETY_K = 2
RERANK_CANDIDATES = 20   # wider set fetched by dense search, before reranking


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


def _rerank(query: str, candidates: List[dict], k: int) -> List[dict]:
    """Re-score candidates with the cross-encoder; return the top-k, best first."""
    if not candidates:
        return []
    scores = get_reranker().predict([(query, c["text"]) for c in candidates])
    for candidate, score in zip(candidates, scores):
        candidate["rerank_score"] = float(score)
    candidates.sort(key=lambda c: c["rerank_score"], reverse=True)
    return candidates[:k]


def user_manual_retrieval(query: str, mvc_code: str, k: int = MANUAL_K) -> List[dict]:
    """
    Retrieve the top-k user-manual chunks for a machine version (dense search over a
    wider candidate set, then cross-encoder rerank).

    Args:
        query: the user's question / symptom (free text).
        mvc_code: the machine's version code (resolved by the agent from machine_id).
        k: number of chunks to return.
    """
    query_vector = get_embedding_model().embed_query(query)
    results = _collection().query(
        query_embeddings=[query_vector],
        n_results=RERANK_CANDIDATES,
        where={"mvc_code": mvc_code},
    )
    return _rerank(query, _format(results), k)


def safety_retrieval(query: str, k: int = SAFETY_K) -> List[dict]:
    """
    Retrieve the top-k safety-guide chunks (dense candidates, then reranked). The
    agent calls this when the query is safety-relevant (hot surfaces,
    fumes/ventilation, electrical, etc.).
    """
    query_vector = get_embedding_model().embed_query(query)
    results = _collection().query(
        query_embeddings=[query_vector],
        n_results=RERANK_CANDIDATES,
        where={"doc_type": "safety"},
    )
    return _rerank(query, _format(results), k)


# =============================================================================
# SELF-TEST — NOT part of the module. Runs one manual query + one safety query
# against the live rag/chroma_store (requires the orchestrator to have run):
#     python rag/retriever.py
# =============================================================================
if __name__ == "__main__":
    def _show(results):
        for r in results:
            m = r["metadata"]
            print(f"  rerank={r.get('rerank_score'):.3f} cos_dist={r['distance']:.4f} "
                  f"{m.get('source_file')} p{m.get('page_start')}-{m.get('page_end')}")
            print(f"      {r['text'][:140].strip()}")

    print("--- user_manual_retrieval('how do I level the bed?', mvc_code='MVC01', k=3) ---")
    _show(user_manual_retrieval("how do I level the bed?", "MVC01", k=3))

    print("\n--- safety_retrieval('fumes and ventilation while printing') ---")
    _show(safety_retrieval("fumes and ventilation while printing"))
