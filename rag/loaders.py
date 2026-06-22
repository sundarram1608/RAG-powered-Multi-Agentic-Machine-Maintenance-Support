"""
loaders.py
----------
Step 1 of the RAG ingestion pipeline: load a PDF and extract its TEXT, page by
page, using raw PyMuPDF.

Design choices (reasoning):
- PyMuPDF: fast, accurate text extraction with correct reading order, page-level
  access, single lightweight dependency; already proven on these exact manuals.
- Page-by-page: preserves an exact page_number per record (for citations and the
  chunker's page-range tracking).
- Text only: the embedding model is text-based; manual images are out of scope
  here (multimodal RAG is a future enhancement).

Returns plain dicts (no LangChain dependency at this step) — conversion to
LangChain Documents happens at the chunking step.
"""

from pathlib import Path
from typing import List, Optional

import fitz  # PyMuPDF


def load_pdf(pdf_path: str, base_metadata: Optional[dict] = None) -> List[dict]:
    """
    Extract a PDF's text, page by page.

    Args:
        pdf_path: path to the PDF file.
        base_metadata: tags attached to every page record (e.g. mvc_code,
            model_name, doc_type) — supplied by the ingest/orchestrator layer.

    Returns:
        One record per non-empty page:
        {
            "text": <page text>,
            "metadata": {
                **base_metadata,
                "source_file": <filename>,
                "page_number": <1-based int>,
            },
        }
    """
    base_metadata = base_metadata or {}
    source_file = Path(pdf_path).name

    pages: List[dict] = []
    doc = fitz.open(pdf_path)
    try:
        for index, page in enumerate(doc):
            text = page.get_text("text")
            if text and text.strip():  # skip blank/cover pages
                pages.append(
                    {
                        "text": text,
                        "metadata": {
                            **base_metadata,
                            "source_file": source_file,
                            "page_number": index + 1,  # 1-based for citations
                        },
                    }
                )
    finally:
        doc.close()

    return pages


# =============================================================================
# SELF-TEST — NOT part of the loader. Run this module directly to sanity-check
# text extraction on one manual:
#     python rag/loaders.py
# =============================================================================
if __name__ == "__main__":
    sample_pdf = "synthetic_data/documents/user_manuals/lulzbot_mini_user_manual.pdf"
    records = load_pdf(
        sample_pdf,
        base_metadata={"mvc_code": "MVC01", "model_name": "LulzBot Mini",
                       "doc_type": "user_manual"},
    )

    print(f"Loaded {len(records)} non-empty pages from {sample_pdf}\n")
    if records:
        first = records[0]
        print("First page metadata:", first["metadata"])
        print("\nFirst page text (first 300 chars):")
        print(first["text"][:300])
