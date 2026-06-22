"""
orchestrator.py
---------------
Entrypoint for the RAG ingestion sub-pipeline. Runs Steps 1-6 end to end to build
the knowledge base (the Chroma index) from the source PDFs.

Run from the project root (virtual environment active):
    python rag/orchestrator.py

This file only *calls* the step functions — no business logic lives here:
  setup (once):    get_embedding_model (3), get_document_mapping (2),
                   get_chroma_collection(reset=True) (6 setup)
  per doc (loop):  load_pdf (1) -> chunk_document (4) -> embed_chunks (5) ->
                   add_chunks (6, appends)
  report (once):   write_chunking_details (chunker fn) -> chunking_details.csv

One-time build; ~30-45 min on CPU for the 5 manuals. Re-running resets and
rebuilds the index from scratch.
"""

from pathlib import Path

from ingest import get_document_mapping
from loaders import load_pdf
from embedding_model_loader import get_embedding_model
from chunker import chunk_document, write_chunking_details
from embeddings import embed_chunks
from vectorstore import get_chroma_collection, add_chunks

CSV_PATH = str(Path(__file__).resolve().parent / "chunking_details.csv")


def main() -> None:
    # ---- SETUP (runs once) ----
    print("Loading embedding model…")
    model = get_embedding_model()                   # Step 3 (cached: loads once)
    mapping = get_document_mapping()                # Step 2 (cached: fetched once)
    collection = get_chroma_collection(reset=True)  # Step 6 setup (clean rebuild)

    print(f"Ingesting {len(mapping)} documents…\n")

    # ---- PER-DOCUMENT LOOP (appends to the collection) ----
    chunks_by_document = {}
    for index, entry in enumerate(mapping, start=1):
        name = Path(entry["pdf_path"]).name
        pages = load_pdf(entry["pdf_path"], entry["base_metadata"])     # Step 1
        chunks = chunk_document(pages, entry["base_metadata"], model)   # Step 4
        vectors = embed_chunks(chunks, model)                           # Step 5
        add_chunks(collection, chunks, vectors)                         # Step 6 (append)
        chunks_by_document[name] = chunks
        print(f"  [{index}/{len(mapping)}] {name}: "
              f"{len(pages)} pages -> {len(chunks)} chunks stored")

    # ---- REPORT (one call to the chunker's CSV function) ----
    write_chunking_details(chunks_by_document, CSV_PATH)

    print(f"\n✅ Ingestion complete: {collection.count()} chunks in "
          f"'{collection.name}' (persisted to rag/chroma_store/)")


if __name__ == "__main__":
    main()
