"""
reranker_loader.py
------------------
Loads the BAAI/bge-reranker-v2-m3 cross-encoder (the reranker that pairs with the
BGE-M3 embedder) once, for QUERY-TIME reranking in the retrieval phase.

This is query-time only: it re-scores (query, chunk-text) pairs and reorders them.
It does NOT touch the stored embeddings, the ingestion pipeline, or the Chroma
index — so adding/removing it never requires re-ingestion.

Device selection is shared with the embedder (env EMBEDDING_DEVICE override → CUDA
→ CPU; MPS is skipped). Runs locally and free, like the embedder.
"""

import os
from functools import lru_cache

from sentence_transformers import CrossEncoder

from embedding_model_loader import _select_device

RERANKER_MODEL_NAME = "BAAI/bge-reranker-v2-m3"
# (query + chunk) truncation length — keeps CPU reranking fast; tunable via env.
RERANK_MAX_LENGTH = int(os.getenv("RERANK_MAX_LENGTH", "512"))


@lru_cache(maxsize=1)
def get_reranker() -> CrossEncoder:
    """Return a cached BGE cross-encoder reranker (loaded once)."""
    device = _select_device()
    print(f"Loading reranker '{RERANKER_MODEL_NAME}' on device: {device}")
    return CrossEncoder(RERANKER_MODEL_NAME, device=device, max_length=RERANK_MAX_LENGTH)
