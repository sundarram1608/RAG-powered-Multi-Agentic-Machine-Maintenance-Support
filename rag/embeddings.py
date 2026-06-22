"""
embeddings.py
-------------
Step 5: embed the chunks for storage.

Takes the chunk Documents from the chunker (Step 4) and turns each chunk's text
into a dense 1024-dim vector using the shared BGE-M3 model (Step 3). These vectors
are what gets stored in ChromaDB (Step 6) and compared against the query vector at
retrieval.

This is the RE-EMBEDDING noted in chunker.py: the throwaway sentence embeddings
used to find breakpoints are NOT reused — each final chunk is embedded here as a
whole (a chunk's embedding is not a combination of its sentence embeddings).

Explicit step (Option A): we compute the vectors here, and Step 6 stores the
precomputed vectors in Chroma. This keeps embedding visible and inspectable, and
gives full control over the storage/query path.
"""

from typing import List

from langchain_core.documents import Document


def embed_chunks(documents: List[Document], embedding_model) -> List[List[float]]:
    """
    Embed chunk Documents into dense vectors.

    Args:
        documents: chunk Documents from chunk_document (Step 4).
        embedding_model: the shared BGE-M3 model (from embedding_model_loader).

    Returns:
        One 1024-dim vector per document, in the same order as the input.
    """
    if not documents:
        return []
    texts = [document.page_content for document in documents]
    return embedding_model.embed_documents(texts)


# =============================================================================
# SELF-TEST — NOT part of the module. Embeds just a FEW mock chunks to verify
# embedding works (the orchestrator embeds all real chunks):
#     python rag/embeddings.py
# =============================================================================
if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))  # put rag/ on path
    from embedding_model_loader import get_embedding_model

    model = get_embedding_model()

    sample_chunks = [
        Document(page_content="Level the bed before starting a print.",
                 metadata={"mvc_code": "MVC01"}),
        Document(page_content="If the hotend shows MINTEMP, check the thermistor wiring.",
                 metadata={"mvc_code": "MVC01"}),
        Document(page_content="Clean the cooling fans every few weeks.",
                 metadata={"mvc_code": "MVC01"}),
    ]

    vectors = embed_chunks(sample_chunks, model)
    print(f"\nEmbedded {len(vectors)} chunks -> {len(vectors)} vectors")
    print(f"Vector dimension: {len(vectors[0])}")
    print(f"First 5 values of chunk 0: {vectors[0][:5]}")
