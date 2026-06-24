# Tuning log

Every tuned dial is recorded here: **when, which knob, old → new, the metric it moved
(before → after), which sweep produced the evidence, and the result file.** This is the
audit trail of *why each dial is set where it is*.

> Scope: this is **5d tuning provenance only** — distinct from 5e (prompt/model
> versioning + the CI regression gate). Policy: sweeps **report**; a change is applied
> only after review, then recorded here **and** as an inline comment at the config site.

## Baseline (2026-06-24, before any tuning)
| Dial | Value | Location |
|---|---|---|
| `RERANK_CANDIDATES` | 8 | `rag/retriever.py` |
| `MANUAL_K` | 5 | `rag/retriever.py` |
| `SAFETY_K` | 2 | `rag/retriever.py` |
| `RERANK_MAX_LENGTH` | 512 | `rag/reranker_loader.py` |
| `MAX_DIAGNOSIS_REQUERIES` | 3 | `agents/config.py` |
| `VERIFY_MAX_ATTEMPTS` | 3 | `agents/config.py` |

## Changes
| Date | Dial | Old → New | Metric (before → after) | Sweep | Result file |
|---|---|---|---|---|---|
| 2026-06-24 | `RERANK_CANDIDATES` | 8 → 8 (no change, validated) | nDCG 0.80→0.88, MRR 0.48→0.55 with rerank ON; cand 12/20 ≤ 8 | reranker_sweep | `eval/results/tuning/reranker_sweep_20260624-081631.xlsx` |

<!-- Template row for an actual change:
| 2026-06-24 | RERANK_CANDIDATES | 8 → 20 | recall@5 0.60 → 0.80 | reranker_sweep | eval/results/tuning/reranker_sweep_...xlsx |
-->

## Findings (observations, not dial changes)
- **Reranker earns its keep on *ranking*, not recall.** rerank ON lifts MRR 0.48→0.55
  and nDCG 0.80→0.88 (right page ranked higher), but recall@k is flat at 0.60 across
  all configs — the reranker only reorders the fetched candidates, it can't recover a
  page that was never retrieved.
- **`RERANK_CANDIDATES=8` is optimal** on this set; 12 and 20 don't improve any metric
  and add latency. Kept at 8.
- **CPU rerank latency ≈ 14s/query** (1.6s → 15.8s). Acceptable for eval; for production
  use a GPU or a hosted rerank API.
- **Recall ceiling (0.60).** ~40% of queries never surface a labelled page even at 20
  candidates → a retrieval-recall gap (chunking / embeddings / `k`), out of scope for
  reranker tuning. Candidate for deeper RAG work.
