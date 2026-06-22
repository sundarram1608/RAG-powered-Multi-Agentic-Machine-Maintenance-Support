"""
vectorstore.py
--------------
Step 6: store the embedded chunks in ChromaDB (persistent), one collection.

Stores the PRECOMPUTED vectors from Step 5 (Option A) — embedding is not done
here. A single collection (`maintenance_manuals`, cosine) holds all 5 PDFs'
chunks, separated by `mvc_code` metadata. Stable ids ({source_file}::{chunk_index})
make re-runs predictable; the orchestrator resets (drops + recreates) the
collection for a clean rebuild.

Note on "cosine": Chroma ranks by cosine DISTANCE (= 1 - cosine similarity), so
query results return distances where SMALLER = more similar.
"""

from pathlib import Path
from typing import List

import chromadb
from langchain_core.documents import Document

PERSIST_DIR = str(Path(__file__).resolve().parent / "chroma_store")
COLLECTION_NAME = "maintenance_manuals"


def get_chroma_collection(reset: bool = False):
    """
    Open the persistent Chroma collection (cosine distance).

    If reset=True, drop and recreate it for a clean rebuild (used by the
    orchestrator so re-ingesting never leaves stale/duplicate chunks).
    """
    client = chromadb.PersistentClient(path=PERSIST_DIR)
    if reset:
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass  # collection didn't exist yet
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def add_chunks(collection, documents: List[Document], vectors: List[List[float]]) -> None:
    """
    Add chunk Documents + their precomputed vectors to the collection.

    Each record: id = "{source_file}::{chunk_index}", document = chunk text,
    embedding = the Step-5 vector, metadata = the chunk's metadata.
    """
    if not documents:
        return  # nothing to store (e.g. a text-less/scanned PDF yielded 0 chunks);
                # Chroma's add() rejects empty lists, so skip.

    ids, texts, metadatas = [], [], []
    for document in documents:
        meta = document.metadata
        ids.append(f"{meta['source_file']}::{meta['chunk_index']}")
        texts.append(document.page_content)
        metadatas.append(dict(meta))

    collection.add(ids=ids, embeddings=vectors, documents=texts, metadatas=metadatas)


# =============================================================================
# SELF-TEST — NOT part of the module. Uses an EPHEMERAL (in-memory) client so it
# does NOT touch the real rag/chroma_store. Stores 3 mock chunks and runs one
# filtered query to confirm storage + cosine search + metadata filtering:
#     python rag/vectorstore.py
# =============================================================================
if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))  # put rag/ on path
    from embedding_model_loader import get_embedding_model
    from embeddings import embed_chunks

    model = get_embedding_model()

    mock = [
        Document(page_content="To level the bed, start the auto-leveling routine.",
                 metadata={"mvc_code": "MVC01", "model_name": "LulzBot Mini",
                           "doc_type": "user_manual", "source_file": "mini.pdf",
                           "page_start": 1, "page_end": 1, "chunk_index": 0}),
        Document(page_content="If the hotend reports MAXTEMP, inspect the heater wiring.",
                 metadata={"mvc_code": "MVC04", "model_name": "LulzBot TAZ Pro",
                           "doc_type": "user_manual", "source_file": "taz_pro.pdf",
                           "page_start": 47, "page_end": 47, "chunk_index": 8}),
        Document(page_content="Operate in a well-ventilated area to limit ultrafine particles.",
                 metadata={"mvc_code": "ALL", "model_name": "All Models",
                           "doc_type": "safety", "source_file": "niosh.pdf",
                           "page_start": 9, "page_end": 9, "chunk_index": 3}),
    ]
    vectors = embed_chunks(mock, model)

    client = chromadb.EphemeralClient()  # in-memory; does not persist
    collection = client.get_or_create_collection(
        name="selftest", metadata={"hnsw:space": "cosine"}
    )
    add_chunks(collection, mock, vectors)
    print(f"\nStored count: {collection.count()}")

    # Filtered query: only MVC01 chunks should be searched.
    query_vector = model.embed_query("how do I level the bed?")
    result = collection.query(
        query_embeddings=[query_vector], n_results=2, where={"mvc_code": "MVC01"}
    )
    print("Filtered query (where mvc_code=MVC01):")
    print(f"  returned ids: {result['ids'][0]}")
    print(f"  top document: {result['documents'][0][0]}")
    print(f"  top distance: {result['distances'][0][0]:.4f}  (smaller = more similar)")
