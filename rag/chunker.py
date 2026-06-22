"""
chunker.py
----------
Step 4: semantic chunking. Splits ONE document's text at topic boundaries
(BGE-M3 sentence similarity, cosine distance), enforces a soft token cap, and
tags each chunk with the page range it covers.

Each PDF is processed independently — its pages are concatenated only with each
other — so chunks never mix content across PDFs.

Note on embeddings (important): the model here embeds SENTENCES only to find the
breakpoints; those embeddings are throwaway. The resulting chunks are RE-EMBEDDED
in Step 5 for storage, because a chunk's embedding is not a combination of its
sentence embeddings. Consequence: semantic chunking does more total embedding
work (sentences + chunks) than recursive chunking — a deliberate trade-off for
topic-coherent chunks.

`langchain-experimental` (SemanticChunker) is being sunset; this module isolates
it, so it can be swapped for a hand-rolled implementation if ever needed.
"""

import csv
import re
from functools import lru_cache
from pathlib import Path
from typing import List

from langchain_core.documents import Document
from langchain_experimental.text_splitter import SemanticChunker
from langchain_text_splitters import RecursiveCharacterTextSplitter

EMBEDDING_MODEL_NAME = "BAAI/bge-m3"
BREAKPOINT_THRESHOLD_TYPE = "percentile"   # cut at the biggest topic shifts
BREAKPOINT_THRESHOLD_AMOUNT = 90           # cut at ~top 10% largest cosine-distance jumps
MAX_CHUNK_TOKENS = 1500                     # soft cap for retrieval precision


@lru_cache(maxsize=1)
def _get_tokenizer():
    """bge-m3 tokenizer (cached) — used to count tokens for the soft cap."""
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(EMBEDDING_MODEL_NAME)


def _count_tokens(text: str) -> int:
    return len(_get_tokenizer().encode(text, add_special_tokens=False))


def _normalize(text: str) -> str:
    """Collapse whitespace to single spaces so chunks are exact substrings."""
    return re.sub(r"\s+", " ", text).strip()


def _concatenate_pages(pages: List[dict]):
    """
    Join a single document's pages into one normalized text and record each
    page's character-offset range (contiguous, including the trailing space).
    """
    parts = []
    page_offsets = []  # (page_number, start, end)
    cursor = 0
    for record in pages:
        norm = _normalize(record["text"])
        if not norm:
            continue
        segment = norm + " "
        start = cursor
        parts.append(segment)
        cursor += len(segment)
        page_offsets.append((record["metadata"]["page_number"], start, cursor))
    return "".join(parts), page_offsets


def _page_at(offset: int, page_offsets) -> int:
    for page_number, start, end in page_offsets:
        if start <= offset < end:
            return page_number
    return page_offsets[-1][0] if page_offsets else None


def chunk_document(pages: List[dict], base_metadata: dict, embedding_model) -> List[Document]:
    """
    Semantic-chunk ONE document and return LangChain Documents with page ranges.

    Args:
        pages: per-page records from loaders.load_pdf (one document only).
        base_metadata: the document's tags (mvc_code, model_name, doc_type).
        embedding_model: the shared BGE-M3 model (from embedding_model_loader).
    """
    if not pages:
        return []

    source_file = pages[0]["metadata"].get("source_file")
    full_text, page_offsets = _concatenate_pages(pages)

    # 1) Semantic split — breakpoints at the largest cosine-distance jumps.
    semantic_chunker = SemanticChunker(
        embedding_model,
        breakpoint_threshold_type=BREAKPOINT_THRESHOLD_TYPE,
        breakpoint_threshold_amount=BREAKPOINT_THRESHOLD_AMOUNT,
    )
    semantic_chunks = semantic_chunker.split_text(full_text)

    # 2) Enforce the soft token cap — re-split any over-cap chunk (semantic + cap).
    capper = RecursiveCharacterTextSplitter(
        chunk_size=MAX_CHUNK_TOKENS,
        chunk_overlap=0,
        length_function=_count_tokens,
    )
    final_chunks = []
    for chunk in semantic_chunks:
        if _count_tokens(chunk) > MAX_CHUNK_TOKENS:
            final_chunks.extend(capper.split_text(chunk))
        else:
            final_chunks.append(chunk)

    # 3) Map page ranges (cumulative cursor over contiguous chunks) + build Documents.
    documents = []
    cursor = 0
    for index, chunk in enumerate(final_chunks):
        position = full_text.find(chunk, cursor)
        if position == -1:
            position = cursor
        start, end = position, position + len(chunk)
        cursor = end
        documents.append(
            Document(
                page_content=chunk,
                metadata={
                    **base_metadata,
                    "source_file": source_file,
                    "page_start": _page_at(start, page_offsets),
                    "page_end": _page_at(max(start, end - 1), page_offsets),
                    "chunk_index": index,
                },
            )
        )
    return documents


def write_chunking_details(chunks_by_document: dict, output_path: str) -> None:
    """
    Write a CSV of chunking details — ONE ROW PER CHUNK:
        document_name, threshold, num_chunks (for that doc), chunk_index, num_tokens

    Called by the orchestrator after chunking all documents (not by the self-test).

    Args:
        chunks_by_document: {document_name: list[Document]} produced by chunk_document.
        output_path: where to write the CSV (e.g. rag/chunking_details.csv).
    """
    fieldnames = ["document_name", "threshold", "num_chunks", "chunk_index", "num_tokens"]

    rows = []
    for document_name, chunks in chunks_by_document.items():
        num_chunks = len(chunks)
        for index, chunk in enumerate(chunks):
            rows.append({
                "document_name": document_name,
                "threshold": BREAKPOINT_THRESHOLD_AMOUNT,
                "num_chunks": num_chunks,
                "chunk_index": index,
                "num_tokens": _count_tokens(chunk.page_content),
            })

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"  ✓ chunking details: wrote {len(rows)} rows to {output}")


# =============================================================================
# SELF-TEST — NOT part of the chunker. Chunks ONE document (the first in the
# mapping) on CPU to verify chunk counts + page ranges. The orchestrator does
# the full 5-PDF pass.
#     python rag/chunker.py
# =============================================================================
if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))  # put rag/ on path
    from ingest import get_document_mapping
    from loaders import load_pdf
    from embedding_model_loader import get_embedding_model

    model = get_embedding_model()
    entry = get_document_mapping()[0]  # one document only (e.g. the Mini)

    pages = load_pdf(entry["pdf_path"], entry["base_metadata"])
    chunks = chunk_document(pages, entry["base_metadata"], model)

    name = Path(entry["pdf_path"]).name
    print(f"\n{name}: {len(pages)} pages -> {len(chunks)} chunks")
    if chunks:
        first, last = chunks[0], chunks[-1]
        print("\nFirst chunk:")
        print(f"  metadata: {first.metadata}")
        print(f"  tokens:   {_count_tokens(first.page_content)}")
        print(f"  text[:200]: {first.page_content[:200]}")
        print(f"\nLast chunk page range: "
              f"p{last.metadata['page_start']}-p{last.metadata['page_end']}")
