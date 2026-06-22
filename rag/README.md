# RAG pipeline — Maintenance Service Assistant

The RAG (Retrieval-Augmented Generation) pipeline gives the **Diagnosis agent its
knowledge** — the troubleshooting, operation, maintenance, and safety content in
the LulzBot manuals + the NIOSH safety guide. Structured facts come from MySQL
(via tools); narrative know-how comes from here.

## Two phases

1. **Ingestion** (offline, build the index once):
  `load PDFs → tag with mvc metadata → semantic chunking → embed (BGE-M3) → store in ChromaDB`
2. **Retrieval** (per query, at agent runtime):
  `embed the query → similarity search in Chroma, filtered by the machine's mvc_code → return top-k passages to the agent`
   *(in `retriever.py`; exposed to the agents as the MCP RAG tools)*

Both phases use the **same embedding model** (BGE-M3) — they must share a vector space.

## Directory

```
rag/
├── loaders.py                # Step 1 — raw PyMuPDF, page-by-page text extraction
├── ingest.py                 # Step 2 — maps each PDF to its mvc_code (reads machine_versions)
├── embedding_model_loader.py # Step 3 — load BGE-M3 once (shared by chunking/embedding/retrieval)
├── chunker.py                # Step 4 — semantic chunking
├── embeddings.py             # Step 5 — embed the chunks
├── vectorstore.py            # Step 6 — ChromaDB (build/persist)
├── orchestrator.py           # entrypoint — runs steps 1 → 6
├── retriever.py              # retrieval phase (query-time)
└── chroma_store/             # generated index (git-ignored)
```

## Ingestion steps

### Step 1 — Loading & extraction (`loaders.py`)

`load_pdf(pdf_path, base_metadata)` opens a PDF with **PyMuPDF** and extracts its
**text, page by page**, returning one record per non-empty page:
`{"text": ..., "metadata": {**base_metadata, "source_file", "page_number"}}`.

- **Why PyMuPDF:** fast, accurate text extraction with correct reading order,
page-level access, single lightweight dependency; already proven on these exact
manuals. (pdfplumber is stronger for tables — which we don't have; pypdf is
lighter but lower-quality text.)
- **Why page-by-page:** preserves an exact `page_number` per record, used for
citations and for the chunker's page-range tracking. Cross-page topics aren't
lost — the chunker stitches across pages and top-k retrieval pulls adjacent
chunks together.
- **Why text-only:** the embedding model is text-based, so images aren't embedded.
Manual diagrams are out of scope here (multimodal RAG is a future enhancement;
separate from the user-photo vision agent).

The loader returns **plain dicts** (no LangChain dependency at this step);
conversion to LangChain `Document`s happens at the chunking step.

#### Run the Step 1 self-test

```bash
# from the project root, with the virtual environment active
python rag/loaders.py
# prints the page count + first-page metadata and a text snippet for one manual
```

### Step 2 — Document mapping & tagging (`ingest.py`)

`get_document_mapping()` builds the list of *(PDF → tags)* the orchestrator
ingests. For each row in **`machine_versions`** (the single source of truth) it
resolves the manual's `manual_path` and tags it with `mvc_code`, `model_name`,
and `doc_type="user_manual"`. The **NIOSH safety guide** isn't a machine version,
so it's appended as a constant and tagged **`mvc_code="ALL"`, `doc_type="safety"`**
(it applies to every machine).

- **Why DB-sourced:** `machine_versions` is the source of truth — onboarding a new
  version there means re-ingesting automatically picks it up (no hardcoding/drift).
- **Why tag every chunk with `mvc_code`:** at retrieval time the agent resolves the
  user's machine → its `mvc_code` (via the `machines` table) and filters Chroma to
  that version's chunks (plus `doc_type="safety"`), so a Mini query never returns
  TAZ Pro content.
- **Missing PDFs:** the PDFs are git-ignored, so if any is absent (e.g. a fresh
  clone) `ingest.py` warns and triggers an automatic download via
  `download_documents.py`.

#### Run the Step 2 self-test
```bash
python rag/ingest.py
# prints the resolved document mapping (mvc_code, doc_type, file) with existence marks
```

### Step 3 — Embedding model loading (`embedding_model_loader.py`)

`get_embedding_model()` loads **BAAI/bge-m3** once (cached via `lru_cache`) through
a LangChain `HuggingFaceEmbeddings` wrapper, and returns it for reuse by the
chunker (Step 4), the embedder (Step 5), and retrieval.

- **Why a shared loader:** bge-m3 is ~2.2 GB and slow to load — loading it once and
  reusing it avoids loading it twice (chunking + embedding) and guarantees a single
  shared vector space.
- **Why BAAI/bge-m3 (SOTA):** an encoder-transformer (XLM-RoBERTa) embedding model,
  retrieval-tuned, **8192-token context** (large chunks never truncate), **1024-dim**
  dense vectors, MIT-licensed.
- **Why a LangChain `Embeddings` wrapper:** LangChain's `SemanticChunker` (Step 4)
  requires this interface, and Chroma + retrieval use it too — one object everywhere.
- **Device (CUDA → CPU; MPS skipped):** uses a cloud GPU (e.g. AWS) if present,
  else CPU. MPS is skipped in auto-selection because for bge-m3's variable-length
  attention it measured ~4x **slower** than CPU on Apple Silicon (Metal kernel
  recompilation). Override with `EMBEDDING_DEVICE` if needed.
- **Normalized embeddings:** bge-m3 retrieval works best with normalized vectors, so
  cosine is the natural similarity (we set Chroma to cosine in Step 6).

> **Deployment note:** running bge-m3 in-process needs real RAM/GPU (fine on a Mac or
> an AWS instance you size yourself), but **not** any
> free tier. The loader is the single seam — to deploy on a constrained host, swap it
> for a hosted embedding API or a small local model and **re-ingest** (query and
> ingestion must use the same model).

#### Run the Step 3 self-test
```bash
python rag/embedding_model_loader.py
# prints the device + embedding dimension (1024); first run downloads bge-m3 (~2.2 GB)
```

### Step 4 — Semantic chunking (`chunker.py`)

`chunk_document(pages, base_metadata, embedding_model)` splits **one document** at
topic boundaries and returns LangChain `Document`s tagged with a page range.

- **Why semantic chunking:** cuts land where the *meaning* shifts (not at arbitrary
  character counts), so each chunk is topic-coherent. Boundaries are found by
  embedding sentences and measuring **cosine distance** between consecutive ones;
  a cut is placed at the **90th-percentile** jumps (the ~10% largest topic shifts in
  that document — relative to the document, not an absolute "90% similarity").
- **Why a ~1500-token soft cap:** bge-m3 wouldn't truncate until 8192, so the cap is
  for **retrieval precision** (huge chunks match loosely) and LLM-context cost. Any
  over-cap semantic chunk is recursively re-split (token count via the bge-m3
  tokenizer) — semantic where possible, capped for safety.
- **Why per-document:** a PDF's pages are concatenated only with each other, so
  chunks never mix content across PDFs. Page ranges (`page_start`/`page_end`) are
  tracked by cumulative offsets over the contiguous chunks.

> **Re-embedding note:** the model is used here only to embed **sentences** to find
> breakpoints — those embeddings are **throwaway**. The resulting **chunks are
> re-embedded in Step 5** for storage, because a chunk's embedding is *not* a
> combination of its sentence embeddings. **Consequence:** semantic chunking does
> more total embedding work (sentences + chunks) than recursive chunking — a
> deliberate trade-off for topic-coherent chunks.
>
> *(`SemanticChunker` comes from `langchain-experimental`, which is being sunset.
> This module isolates it, so it can be swapped for a hand-rolled implementation if
> needed.)*

#### Run the Step 4 self-test
```bash
python rag/chunker.py
# chunks ONE document (the Mini) on CPU for verification (~5-6 min);
# the orchestrator does the full 5-PDF pass
```

### Step 5 — Embedding the chunks (`embeddings.py`)

`embed_chunks(documents, embedding_model)` turns each chunk's text into a dense
**1024-dim vector** using the shared BGE-M3 model, returning one vector per chunk
(in input order).

- **Why an explicit step (Option A):** we compute the vectors here, and Step 6
  stores the precomputed vectors in Chroma (raw `chromadb`). This keeps embedding
  **visible and inspectable** and gives full control over the storage/query path
  (vs the `langchain-chroma` wrapper, which would hide embedding inside the insert).
- **This is the re-embedding** noted in Step 4: the throwaway sentence embeddings
  aren't reused — each final chunk is embedded here as a whole.
- **Same model everywhere:** the chunks (now) and the query (at retrieval) are
  embedded by the same BGE-M3 model → one shared vector space.

#### Run the Step 5 self-test
```bash
python rag/embeddings.py
# embeds a few mock chunks and prints the count + vector dimension (1024)
```

### Step 6 — Storing in ChromaDB (`vectorstore.py`)

`get_chroma_collection(reset)` + `add_chunks(collection, documents, vectors)` store
the chunks in a persistent Chroma collection.

- **One collection** (`maintenance_manuals`, **cosine** distance) holds all 5 PDFs'
  chunks, persisted to `rag/chroma_store/` (git-ignored). Separation is by
  `mvc_code` metadata.
- **Each record:** `id` = `{source_file}::{chunk_index}` (stable, readable),
  `document` = chunk text, `embedding` = the precomputed Step-5 vector,
  `metadata` = `{mvc_code, model_name, doc_type, source_file, page_start, page_end, chunk_index}`.
- **Precomputed vectors (Option A):** we pass the Step-5 vectors to `add()` — Chroma
  does not re-embed on insert.
- **Reset on full ingest:** the orchestrator calls `get_chroma_collection(reset=True)`
  to drop + recreate the collection, so re-ingesting never leaves stale chunks.
- **Cosine = distance:** Chroma reports cosine *distance* (`1 - similarity`), so query
  results come back as distances where **smaller = more similar**.

#### Run the Step 6 self-test
```bash
python rag/vectorstore.py
# uses an in-memory client (won't touch chroma_store): stores 3 mock chunks and
# runs one mvc_code-filtered query to confirm storage + search + filtering
```

---

## Building the knowledge base — the ingestion sub-pipeline (`orchestrator.py`)

`orchestrator.py` is the entrypoint that runs Steps 1–6 end to end to build the
Chroma index. It only *calls* the step functions (no logic of its own):

- **Setup (once):** `get_embedding_model()` (3), `get_document_mapping()` (2),
  `get_chroma_collection(reset=True)` (6 — clean rebuild).
- **Per document (loop):** `load_pdf` → `chunk_document` → `embed_chunks` →
  `add_chunks` (appends to the collection).
- **Report (once):** `write_chunking_details(...)` → `rag/chunking_details.csv`.

It's a **one-time build** (~30–45 min on CPU for the 5 manuals). Re-running resets
and rebuilds the index from scratch.

### How to build the knowledge base (run this once)

> Run all commands from the **project root**.

1. **Activate the virtual environment**
   ```bash
   source preventivemaintenance3.11/bin/activate
   ```
2. **Prerequisites**
   - Dependencies installed: `pip install -r requirements.txt`
   - MySQL running and the `maintenance` database **already built** — run the data
     layer first: `python synthetic_data/tables/generate_data.py`. (Ingestion reads
     the PDF↔`mvc_code` mapping from the `machine_versions` table.)
   - `.env` filled in with your DB credentials.
   - Source PDFs present. If any are missing, ingestion **auto-downloads** them; or
     fetch them yourself: `python synthetic_data/documents/download_documents.py`.
3. **Confirm the database connection** (uses the same credentials ingestion does)
   ```bash
   python synthetic_data/tables/db_connection.py
   # expect: ✅ Connected — MySQL 8.x.x, database: maintenance
   ```
   > Use this check, **not** `mysqladmin ping` — `mysqladmin` with no `-u/-p` tries
   > your OS username and fails with "Access denied" even when the server is fine.
4. **Run the ingestion**
   ```bash
   python rag/orchestrator.py
   ```
   > On the **first run**, this downloads the BGE-M3 embedding model (~2.2 GB) from
   > Hugging Face (needs internet); it's cached locally for later runs.
5. **Expected output**
   ```
   Loading embedding model…
   Ingesting 5 documents…
     [1/5] lulzbot_mini_user_manual.pdf: 83 pages -> 66 chunks stored   (counts illustrative)
     [2/5] lulzbot_taz6_user_manual.pdf: ... chunks stored
     ...
     ✓ chunking details: wrote N rows to .../chunking_details.csv
   ✅ Ingestion complete: <N> chunks in 'maintenance_manuals' (persisted to rag/chroma_store/)
   ```
   Takes ~30–45 min on CPU (one-time).
6. **Verify**
   - `rag/chroma_store/` now exists — the persisted vector index.
   - `rag/chunking_details.csv` lists per-document chunk counts + token sizes.

---

## Retrieval phase (`retriever.py`)

The retrieval side of RAG — what the agent calls at query time. Two single-purpose
functions, each embeds the query with the **same BGE-M3 model** and runs a
metadata-filtered cosine search over the persisted Chroma index:

- **`user_manual_retrieval(query, mvc_code, k=5)`** → top-k **manual** chunks for that
  machine version (`where={"mvc_code": mvc_code}`).
- **`safety_retrieval(query, k=2)`** → top-k **safety-guide** chunks
  (`where={"doc_type": "safety"}`).

Each returns `{text, metadata, distance}` (smaller distance = more similar);
`metadata` carries `source_file` + `page_start/page_end` for citations.

- **Two separate functions (not one combined search):** a combined `mvc OR safety`
  search could let manual chunks crowd safety out of the top-k. Separate functions
  guarantee each source is searched on its own terms.
- **Conditional safety:** the **agent decides** whether to call `safety_retrieval`
  (e.g. only for safety-relevant queries) — keeps non-safety queries free of safety
  noise. (Always-call vs LLM-decides is an agent-layer policy.)
- **`mvc_code` is passed in** — the `machine_id → mvc_code` lookup is an agent/tool
  concern, not the retriever's.
- **Generation is the agent's job** — this module only returns context; the agent
  assembles the prompt and calls the LLM.
- The collection handle and embedding model are cached, so repeated queries are fast
  (model loads once).

#### Run the retrieval self-test
```bash
python rag/retriever.py
# runs one manual query (MVC01) + one safety query against the live chroma_store
# (requires the orchestrator/ingestion to have run first)
```