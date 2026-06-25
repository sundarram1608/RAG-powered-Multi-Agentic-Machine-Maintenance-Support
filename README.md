# Agentic RAG + MCP — FDM Service Assistant

A multi-agent AI workflow for **manufacturing equipment troubleshooting, maintenance and service**, built with
LangGraph (orchestration), RAG over a vector database (knowledge), and MCP (tools/actions).

> Status: ✅ Phases 0–5 complete — 12-agent LangGraph workflow + observability, evaluation & governance (LangSmith). Streamlit UI (Phase 6) next.

---
## Building the Project from Scratch

### 0. Prerequisites:  
> - **Python 3.11** (this project was created with Python 3.11.6)
> - a local **MySQL Community Server**
> - macOS / Linux / Windows with the `venv` module (bundled with Python)

Run all commands from the project root.

--- 

### 1. Environment — virtual environment + dependencies
**Check your Python version:**

```bash
python3 --version   # expect Python 3.11.x
```

**Create the virtual environment**

The environment is named **`preventivemaintenance3.11`** (the `3.11` reflects the Python version).

```bash
# from the project root: .../agentic_ai_projects/agenticragmcp
python3 -m venv preventivemaintenance3.11
```

**Activate the environment**

**macOS / Linux (zsh / bash):**

```bash
source preventivemaintenance3.11/bin/activate
```

**(Optional) Upgrade pip**

```bash
python -m pip install --upgrade pip
```

**Install dependencies**

`requirements.txt`

```bash
pip install -r requirements.txt
```

**Deactivate when done**

```bash
deactivate
```

**Consolidated list of codes for environment setup** 
```bash
python3 --version
python3 -m venv preventivemaintenance3.11
source preventivemaintenance3.11/bin/activate
pip install -r requirements.txt
deactivate
```

---

### 2. Knowledge Base

The **Knowledge Base** has two layers and both feed the context to the LLM agents: 
- **Database** (structured facts/ SQL Tables) 
- **RAG** (user manuals & Safety documents/ Vector store).

> **2a. Database layer** — MySQL tables + seed data + schema metadata.
> The database layer is built with well though out tables that provide the structured knowledge base to the agentic LLMs. This layer is hosted in MySQL server.
> It's dual-purpose — a knowledge source *and* the operational system of record. Agent tools **read** facts from it **and write** to it at runtime (logging incidents, booking technician slots, recording incident outcomes). Only the `incidents` and `technician_schedule` tables are ever written; all other tables are read-only.
>  - Full guide → [`synthetic_data/README.md`](synthetic_data/README.md)
>  - (MySQL install/setup → [`synthetic_data/tables/readme_database_creation.md`](synthetic_data/tables/readme_database_creation.md))

  ```bash
  python synthetic_data/tables/generate_data.py
  ```
<br>

> **2b. RAG layer** — vector index built from the source PDFs.
> The RAG knowledge base is built from publicly available, legally reusable documents in
> [`synthetic_data/documents/`](synthetic_data/documents/):
> - **User manuals (FDM)** — LulzBot Mini, TAZ 6, TAZ Workhorse, TAZ Pro (CC BY-SA 4.0)
> - **Safety guidelines** — NIOSH *Approaches to Safe 3D Printing* (public domain)
> See [`synthetic_data/documents/ATTRIBUTIONS.md`](synthetic_data/documents/ATTRIBUTIONS.md) for full source URLs and license terms.
> RAG is **read-only** knowledge (the manuals).
> The source PDFs are not committed to version control (see `.gitignore`).
>  - Full guide to build the RAG layer → [`rag/README.md`](rag/README.md)
  
  ```bash
  python rag/orchestrator.py
  ```
  <br>
  
**Note on the Database layers:**  Both the layers are consulted for knowledge but, only the DB is mutated at runtime. 

---

### 3. MCP tool layer

The **tools** are the only way the agents act on the Knowledge Base. Each tool is a plain Python function in `mcp_server/mcp_tools/`;
`mcp_server/server.py` registers them with **FastMCP**, which turns each function's name + docstring + type hints into the schema the LLM sees.

**16 tools, in four groups:**
- **read (9)** — `get_machine`, `get_overdue_status`, `get_maintenance_history`, `get_incident_history`, `get_incident`, `list_incidents`, `check_inventory`, `find_available_technician`, `list_available_technicians` (DB reads).
- **rag (2)** — `user_manual_retrieval`, `safety_retrieval` (thin wrappers over `rag/retriever.py`).
- **write (3)** — `create_incident`, `book_technician_slot`, `update_incident` (scoped writes to `incidents` / `technician_schedule` only).
- **other (2)** — `run_readonly_query` (LLM-generated read-only SQL), `send_email` (notifications from "Agentic FDM Services").

**Two MCP transports** (both operational — see `mcp_server/README.md`):
- **stdio** (default) serves the 14 local-data tools (read + rag + write); the agent auto-spawns it.
- **streamable-HTTP** (`127.0.0.1:8000`) serves the 2 "service" tools (`run_readonly_query`, `send_email`) as a separate process.

**Safety & PII**: three MySQL identities — admin, `maint_readonly` (SELECT only, for generated SQL), and `maint_write` (INSERT/UPDATE on the two
mutable tables only, no DELETE/DDL/master data). Generated SQL is validated (read-only, single statement, no `phone`); writes go only through the scoped tools; `phone` never enter the agent's context.

```bash
# one-time: create the read-only + write DB users (writes creds into .env)
python mcp_server/setup_db_users.py

# start the HTTP "services" server (separate process)
python mcp_server/server.py http        # -> http://127.0.0.1:8000/mcp
# (the stdio server is auto-spawned by the agent; run by hand: python mcp_server/server.py)

# smoke test — list the tools each transport exposes
python mcp_server/server.py --selftest  # expect 14 stdio + 2 http tools
```
For live `send_email`, also set `AGENT_EMAIL` + `AGENT_EMAIL_APP_PASSWORD` in `.env`.
Full guide → [`mcp_server/README.md`](mcp_server/README.md)

---

### 4. Agent layer — LangGraph workflow

**12 specialized agents** are wired into one **LangGraph `StateGraph`** (14 nodes),
compiled with a `MemorySaver` checkpointer. The Input guard screens every turn; the
**Supervisor** routes to one of four sub-flows:

- **troubleshoot** — Intake (resolve machine + symptom) → Diagnosis (RAG manual/safety + DB facts, corrective-RAG) → Verifier (independent RAG-triad + safety judge) → the `needs_technician` gate → **Self Action** (operator self-fix) / **Technician Action** (book + notify) / Decider.
- **analytics** — text-to-SQL Generator → independent Reviewer → execute (read-only) → answer.
- **manage_incident** — resolve → approve → execute (close / assign / comment).
- **general** — a capability/greeting reply.

Everything converges on a single **Output** agent (the "voice"), which renders
fact-bearing replies from templates (no hallucinated ids/counts) and a final PII
scrub. **Human-in-the-loop** pauses (clarifications, the self/technician decision,
the two-button self-fix, manage approval) use LangGraph `interrupt()` / resume.

**Models (all free-tier):** Groq **Llama 3.3 70B** (reasoning / tool-calling),
**Gemini 2.5 Flash-Lite** (independent judge) with a **Qwen-3 32B on Groq** fallback
when Gemini is unavailable, **BGE-M3** (local embeddings) + reranker. Both LLMs use
retry/backoff. Needs `GROQ_API_KEY` + `GOOGLE_API_KEY` in `.env`.

```bash
# start the HTTP services server first (separate terminal)
python mcp_server/server.py http

# interactive CLI (one process = one conversation)
python agents/run.py

# or the end-to-end journeys / deterministic routing checks
python agents/test_e2e.py
python agents/test_routing.py
```
Full guide (topology, every edge, interrupts, turn/memory model) → [`agents/README.md`](agents/README.md)

### 5. Observability, Evaluation & Governance (Phase 5)

**Purpose:** make the workflow **observable, measurably correct, and governed** — all
**backstage**: none of this changes the live agent's behaviour. Three pillars:

**A. Observability — [`observability/`](observability/) (5a).** Every turn is traced to
LangSmith (run tree, latency, tokens, cost), grouped by conversation/turn, with **PII
masked** before upload.

**B. Evaluation — [`eval/`](eval/) (5b → 5d).** Golden datasets (5b) → evaluators +
`run_eval` + an **Excel scorecard** + LangSmith Experiments (5c) → **tuning** sweeps for
the reranker/verifier/diagnosis dials (5d). Grades RAG groundedness, retrieval, routing,
SQL, safety red-team, and incident management. The eval **judge** runs on OpenRouter
(separate quota; the live agent never calls it).

**C. Governance — [`eval/versioning_and_ci/`](eval/versioning_and_ci/) + [`observability/governance.py`](observability/governance.py) (5e → 5f).**
Version-stamp every experiment + a **regression gate** that fails CI on a quality drop
(5e); **human feedback capture** + a **review queue** that routes risky runs
(low-confidence / escalations / DB writes) to a human (5f). PII masking is also a
governance control.

> **Grouping notes (as asked):** **Versioning + CI** sits under **Governance** (it's a
> change/process control, though it consumes Evaluation outputs). **Safety** is
> *cross-cutting*, not a standalone phase — its *evaluation* (input-guard red-team +
> PII-leak checks) lives under **Evaluation** (`safety_redteam`); its *enforcement* (PII
> masking, the input guard) lives under **Governance/Observability**.

**Evaluations carried out — what, where to see them, and what they validate.** Each
dataset is graded into a **LangSmith Experiment** (per-example drill-down) and a sheet
in `eval/results/eval_<ts>.xlsx`; tuning sweeps write to `eval/results/tuning/`.

| Evaluation | What it measures | Where to see the result | What it validates in the workflow |
|---|---|---|---|
| **Troubleshoot** (faithfulness*, answer-relevance*, needs-technician gate) | diagnosis grounded in retrieved context, answers the symptom, correct self-vs-technician gate | LangSmith `fdm-troubleshoot` + Excel `troubleshoot_cases` | Diagnosis → Verifier → `needs_technician` gate |
| **Retrieval** (precision@k, recall@k, MRR, nDCG) | retriever surfaces the right manual pages | LangSmith `fdm-retrieval` + Excel `retrieval_labels` | RAG retriever (`rag/retriever.py`) feeding Diagnosis |
| **Text-to-SQL** (rows-match, read-only, no-phone) | analytics SQL is correct, read-only, never touches `phone` | LangSmith `fdm-sql` + Excel `sql_cases` | Analytics generate → Reviewer → execute |
| **Routing** (intent accuracy) | supervisor routes to the right branch | LangSmith `fdm-routing` + Excel `routing_cases` | Supervisor (+ Intake/Decider) |
| **Safety** (guard-correct, no-PII-leak) | input guard refuses unsafe/PII/injection; no leak | LangSmith `fdm-safety` + Excel `safety_redteam` | Input guard + Output PII scrub |
| **Manage incident** (action-correct) | resolver picks the right action / approval | LangSmith `fdm-manage` + Excel `manage_cases` | Manage-Incident resolver |
| **Reranker sweep** (tuning) | does reranking help + best `RERANK_CANDIDATES` | `eval/results/tuning/reranker_sweep_*.xlsx` + `TUNING_LOG.md` | `RERANK_CANDIDATES` dial in `rag/retriever.py` |
| **Verifier calibration** (tuning) | is the Verifier too strict / too lax | `eval/results/tuning/verifier_calibration_*.xlsx` | Verifier strictness / `VERIFY_MAX_ATTEMPTS` |
| **Diagnosis sweep** (tuning) | do extra corrective-RAG retries pay off | `eval/results/tuning/diagnosis_sweep_*.xlsx` | `MAX_DIAGNOSIS_REQUERIES` dial |
| **Regression gate** (CI) | blocking metrics vs blessed baseline | `ci_gate.py` exit 0/1 + console | guards changes to all of the above |

<sub>\* faithfulness/answer-relevance use the free OpenRouter judge → may show `n/a` under rate-limits; they are **advisory** (never block the CI gate). All other metrics are deterministic.</sub>

**Run order (fresh fork, after steps 0–4). Prereqs: `.env` has `GROQ_API_KEY`,
`GOOGLE_API_KEY`, `LANGSMITH_API_KEY` (+ `OPENROUTER_API_KEY` for the eval judge); the DB
is up; the RAG index is built.**
```bash
# 5a — observability (tracing auto-on once LANGSMITH_TRACING=true)
python observability/trace_smoke.py

# 5b — datasets: validate, verify gold SQL vs DB, upload to LangSmith
python eval/build/validate_datasets.py
python eval/build/derive_sql_expectations.py
python eval/build/upload_datasets.py

# 5c — evaluation: graders -> Experiments + eval/results/eval_<ts>.xlsx
python mcp_server/server.py http              # separate terminal (troubleshoot/manage need it)
python eval/run_eval.py                       # ⚠ Groq free cap is 100k tokens/day — spread datasets across days
#   or per-dataset, e.g.: python eval/run_eval.py --dataset routing

# 5d — tuning (reranker is free/offline; the other two need the server + judge)
python eval/tuning/reranker_sweep.py
python eval/tuning/verifier_calibration.py
python eval/tuning/diagnosis_sweep.py

# 5e — versioning + regression gate (reads LangSmith; zero tokens)
python eval/versioning_and_ci/ci_gate.py --bless   # bless baseline from valid runs
python eval/versioning_and_ci/ci_gate.py           # gate: exit 0 (pass) / 1 (regression)

# 5f — governance: review-queue flagging is AUTOMATIC during 5c/runtime;
#      feedback (observability.log_feedback) is called by the Phase 6 UI
```
Full guides: [`observability/README.md`](observability/README.md) and [`eval/README.md`](eval/README.md).

> **Reproducibility:** the datasets, build steps, and **deterministic** metrics
> (routing/SQL/retrieval/safety/manage) reproduce exactly. **LLM-judge** metrics
> (faithfulness/answer-relevance) vary run-to-run (model nondeterminism + free-tier rate
> limits), so exact *scores* aren't bit-reproducible — the *structure and deterministic
> results* are.

### 6. Application (Phase 6) — *(coming soon)*
A **Streamlit UI** for operators/technicians/supervisors. A **skeleton** already exists
in [`app/`](app/) (`main.py` + `app_utils.py`) — the chat shell is wired, but
`run_agent()` is a stub; Phase 6 connects it to `agents/api.py`
(`start_turn`/`resume_turn`, which stream progress and return `run_id` for feedback).

---

## Notes

- The `preventivemaintenance3.11/` folder is the virtual environment and should **not** be committed to
  version control. Add it to `.gitignore`:

  ```gitignore
  preventivemaintenance3.11/
  ```

- If you ever need to start fresh, delete the folder and recreate it:

  ```bash
  rm -rf preventivemaintenance3.11
  python3 -m venv preventivemaintenance3.11
  ```
