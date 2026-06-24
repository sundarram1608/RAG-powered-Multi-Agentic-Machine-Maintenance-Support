"""
inspect_corpus.py — derive honest page citations for the golden datasets.

Scans the indexed chunk TEXT in the Chroma store (no embedder, no PDF scan) for
topic keywords and prints the matching chunks' (source_file, page range, snippet).
Used at build time to label `retrieval_labels` relevant pages and `troubleshoot_cases`
cited_pages from the real corpus.

    python eval/build/inspect_corpus.py "thermistor" MVC02
    python eval/build/inspect_corpus.py            # runs the built-in topic sweep
"""

import sys
from pathlib import Path

import chromadb

PERSIST = str(Path(__file__).resolve().parents[2] / "rag" / "chroma_store")
COLLECTION = "maintenance_manuals"

_cache = None


def _all():
    global _cache
    if _cache is None:
        col = chromadb.PersistentClient(path=PERSIST).get_collection(COLLECTION)
        _cache = col.get(include=["documents", "metadatas"])
    return _cache


def search(keywords, mvc=None, doc_type=None, n=3):
    """Chunks whose text contains ANY keyword (case-insensitive), optionally filtered
    by mvc_code / doc_type. Returns [(source_file, page_start, page_end, snippet)]."""
    data = _all()
    kws = [k.lower() for k in (keywords if isinstance(keywords, (list, tuple)) else [keywords])]
    hits = []
    for doc, meta in zip(data["documents"], data["metadatas"]):
        if mvc and meta.get("mvc_code") != mvc:
            continue
        if doc_type and meta.get("doc_type") != doc_type:
            continue
        low = (doc or "").lower()
        if any(k in low for k in kws):
            i = min((low.find(k) for k in kws if k in low), default=0)
            snippet = " ".join(doc[max(0, i - 20):i + 90].split())
            hits.append((meta["source_file"], meta["page_start"], meta["page_end"], snippet))
    hits.sort(key=lambda h: (h[0], h[1]))
    return hits[:n]


def _show(topic, keywords, **kw):
    print(f"\n### {topic}  (kw={keywords} {kw})")
    for sf, ps, pe, snip in search(keywords, **kw):
        print(f"  {sf} p{ps}-{pe}: {snip[:90]}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        kw = sys.argv[1]
        mvc = sys.argv[2] if len(sys.argv) > 2 else None
        _show(kw, kw, mvc=mvc, n=6)
    else:
        _show("bed_adhesion_mini", ["first layer", "adhes", "leveling"], mvc="MVC01")
        _show("heated_bed_taz6", ["thermistor", "heated bed", "bed temperature"], mvc="MVC02")
        _show("nozzle_clog", ["clog", "cold pull", "nozzle"], mvc="MVC01")
        _show("filament_feed", ["filament", "extruder"], mvc="MVC02")
        _show("z_offset", ["offset", "calibrat"], mvc="MVC01")
        _show("safety_ventilation", ["ventilation", "emission", "fume", "particle"], doc_type="safety")
        _show("safety_burn", ["hot", "burn", "temperature"], doc_type="safety")
