"""
embedding_model_loader.py
-------------------------
Step 3 of the RAG ingestion pipeline: load the embedding model ONCE and share it.

Loads BAAI/bge-m3 through a LangChain HuggingFaceEmbeddings wrapper, on the best
available device (CUDA > CPU; MPS deliberately skipped — see `_select_device`),
with normalized embeddings (so cosine is the natural similarity). The model is
cached (lru_cache) so the ~2.2 GB weights load
only once per process, then are reused by:
  - Step 4 (semantic chunking — sentence-similarity breakpoints)
  - Step 5 (embedding the final chunks)
  - retrieval (embedding the user's query)

Using the SAME model everywhere => one shared vector space (required for retrieval).

Device note: CUDA covers cloud GPUs (e.g. AWS g5) and CPU is the universal
fallback, so this runs unchanged across machines. MPS (Apple Silicon) is skipped
in auto-selection because it measured ~4x slower than CPU for bge-m3 here; set
EMBEDDING_DEVICE to force a device (e.g. "mps"/"cpu"/"cuda").
"""

import sys
from functools import lru_cache

EMBEDDING_MODEL_NAME = "BAAI/bge-m3"

# Bound the input length so pathologically long inputs (e.g. a PDF table parsed as
# one giant "sentence" during semantic chunking) don't blow up attention memory on
# MPS. Must be >= the chunker's MAX_CHUNK_TOKENS (1500) so real chunks are never
# truncated; a modest batch size keeps peak memory in check.
MAX_SEQ_LENGTH = 1536
ENCODE_BATCH_SIZE = 8


def _select_device() -> str:
    """
    Device for embedding. Preference: CUDA (cloud GPU) > CPU.

    MPS (Apple GPU) is deliberately skipped in auto-selection: for bge-m3's
    variable-length attention, MPS measured ~4x SLOWER than CPU here (Metal
    recompiles kernels per sequence shape). Set EMBEDDING_DEVICE to override
    (e.g. "mps" or "cpu") if you want to force a specific device.
    """
    import os

    import torch

    override = os.getenv("EMBEDDING_DEVICE")
    if override:
        return override
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


@lru_cache(maxsize=1)
def get_embedding_model():
    """
    Return a cached LangChain Embeddings object for BAAI/bge-m3.

    lru_cache(maxsize=1) guarantees the model is loaded exactly once per process;
    every caller (chunker, embedder, retriever) gets the same instance.
    """
    from langchain_huggingface import HuggingFaceEmbeddings

    device = _select_device()
    # stderr, NOT stdout — stdout is the stdio-MCP JSON-RPC channel when a tool
    # that triggers model loading runs inside the stdio server.
    print(f"Loading embedding model '{EMBEDDING_MODEL_NAME}' on device: {device}",
          file=sys.stderr)
    model = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL_NAME,
        model_kwargs={"device": device},
        encode_kwargs={"normalize_embeddings": True, "batch_size": ENCODE_BATCH_SIZE},
    )
    # Cap the underlying model's input length so over-long inputs are truncated
    # rather than exhausting MPS/GPU memory. (Private attr, but stable.)
    try:
        model._client.max_seq_length = MAX_SEQ_LENGTH
    except Exception:
        pass
    return model


# =============================================================================
# SELF-TEST — NOT part of the loader. Verifies the model loads, the device, and
# the embedding dimension (expect 1024 for bge-m3):
#     python rag/embedding_model_loader.py
# (First run downloads the ~2.2 GB model from Hugging Face; later runs use cache.)
# =============================================================================
if __name__ == "__main__":
    model = get_embedding_model()

    sample = "The hotend is not heating and shows a MINTEMP error."
    vector = model.embed_query(sample)

    print(f"Embedded a sample query -> dimension: {len(vector)}")
    print(f"First 5 values: {vector[:5]}")
    # Confirm the cache returns the same loaded instance (no second load).
    print("Cached instance reused:", get_embedding_model() is model)
