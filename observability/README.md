# Observability layer (LangSmith)

This layer makes every run of the agent workflow **observable**: each turn is captured as a structured trace in [LangSmith](https://smith.langchain.com), with
PII masked, organised by conversation and turn, and annotated with the turn's outcome. It is **backstage** — for *developers*.

---

## 1. Backstage vs frontstage — two different things

There are two ways to "see what the agents are doing", and they are **separate mechanisms** that happen to read the same graph run:

| | Backstage (Observability layer) | Frontstage (App Layer) |
|---|---|---|
| **Audience** | developers / operators of the system | end users (operator / technician / supervisor) |
| **Where** | LangSmith web UI (`smith.langchain.com`) | the Streamlit chat app |
| **Shows** | full run tree, latency, tokens, cost, errors, metadata | "thinking… calling tools… answer" progress + the final reply |
| **How** | LangChain tracer → LangSmith (this folder) | LangGraph `astream` events → UI |
| **PII** | masked before storage (devs read it) | already scrubbed by the Output node |

**End users never see LangSmith.** It is an internal observability tool.

---

## 2. What gets traced — the run tree

When tracing is on, **one invoke = one trace** (a "run tree"):

```
root run = one invoke (a start_turn, or a single resume_turn)   START → END
├─ child run = each graph node (input, supervisor, diagnosis, …)
│   └─ leaf run = each LLM call / MCP tool call inside that node
└─ …
```

We do **not** create spans. LangGraph's tracer builds the whole tree automatically once a tracer is attached; this module only *attaches* the tracer, *tags* the runs, and *masks* their contents.

> LangSmith calls these "runs"; they are the equivalent of OpenTelemetry "spans".

---

## 3. The three ids — and how turns are told apart

A conversation can contain many turns, and one turn can span several traces (a clarification pauses the graph and each resume is a separate invoke). Three ids
keep this legible:

| Id | Scope | Set by |
|---|---|---|
| `thread_id` | one **conversation** (groups many turns) | the chat session (also the checkpointer key) |
| `turn_id` | one **request + its clarification resumes** | minted at `start_turn`, **reused** across that turn's resumes |
| run (trace) | one **invoke** (a start, or a single resume) | the tracer, automatically |

- **Simple turn** (no interrupt) → exactly **1 trace**.
- **Turn with a clarification / decision / approval** → **2+ traces** (the start +
  each resume), all sharing the same `turn_id`.
- In LangSmith's **Threads** view, traces group by `session_id = thread_id`; within
  a thread you filter/group by `turn_id` to collapse a turn's start+resumes into one
  logical unit. `run_name` (`turn:start` / `turn:resume`) labels them visually.

So: `thread_id` answers "which conversation?", `turn_id` answers "which turn?", and the trace is "one step of that turn".

---

## 4. Metadata attached to every trace

**At creation** (`make_config`): `session_id`, `thread_id`, `turn_id`, `user_id`, and the `models` in use (reasoning / judge / judge_fallback) + tag `fdm-agentic`.

**After the turn** (`enrich_run` → `client.update_run`): the **outcome**, so you can filter on it — `intent`, `machine_id`, `decision_path`, `needs_technician`,
`verdict_score`, `verdict_approved`, `verifier_exhausted`, `action`, `prompt_versions`.

This is what lets you ask LangSmith things like *"all troubleshoot turns for M01 where the verifier rejected"* or *"every escalation to a technician this week"*.
(Per-node detail — corrective-RAG re-queries, whether the Qwen judge fallback fired
— is visible inside the run tree's child spans.)

---

## 5. PII masking

Developers read trace contents, so PII is masked **before** anything is uploaded: a recursive redactor (`_redact`) strips emails and 7+‑digit phone numbers from all run inputs/outputs, wired into the LangSmith client via `hide_inputs` / `hide_outputs`.

This is defense-in-depth on top of the existing guarantees:
1. the DB tools never return `phone` (stripped at source),
2. the Output node scrubs PII from the final reply,
3. **this layer** masks anything that slips through — e.g. a phone/email a *user* types into their message. `trace_smoke.py` proves it by planting a phone+email in the input and asserting they're redacted in the stored run.

---

## 6. How it ties to the app (data flow)

The UI never talks to LangSmith. Tracing is process-level; whoever calls the graph (CLI, tests, or the app) is traced. The UI's only role is to supply the ids + message.

```
Operator types in the UI
  └─ app calls  api.start_turn(thread_id, user_id, message) # or resume_turn(..., turn_id)
       └─ observability.make_config → {thread_id, turn_id, user_id, run_id, metadata, tags, masking tracer}
            └─ app_graph.ainvoke(input, config) (Phase 6: astream)
                 ├─ FRONTSTAGE: astream events → UI progress + reply (App Layer)
                 └─ BACKSTAGE: tracer auto-captures the run tree → LangSmith (this layer)
       └─ observability.enrich_run(run_id, metadata, final_state) # outcome on the trace
```

`thread_id` comes from the chat session, `user_id` from login, `turn_id` is minted per request (reused while paused). The `observability/` package sits **beside** `agents/api.py`, not inside the UI.

---

## 7. Setup

1. **Create a free LangSmith account** → https://smith.langchain.com (Developer tier:
   1 seat, ~5k traces/month — verify current limits). **Settings → API Keys → Create**.
2. **Put the keys in `.env`** (git-ignored; never commit):
   ```
   LANGSMITH_TRACING=true
   LANGSMITH_API_KEY=lsv2_pt_xxxxx
   LANGSMITH_PROJECT=fdm-agentic
   # LANGSMITH_ENDPOINT=https://api.smith.langchain.com   # eu.api... if your org is EU
   ```
3. **Verify** — see §8 below.

Tracing is **gated by `LANGSMITH_TRACING`** — set it to anything but `true` and this layer becomes a no-op (the app runs identically, untraced). No node code depends on it.

## 8. How to check it's working

### 8.1 Automated smoke test
```bash
python observability/trace_smoke.py
```
It runs one cheap traced turn (a refusal — `input → output`, a single LLM call, **no MCP server needed**) with a phone + email planted in the message, then reads the run back from LangSmith and checks the masking. Expected output:
```
kind   : answer
reply  : I can only help with FDM 3D-printer maintenance and service ...
run_id : becf1436-9c40-475f-9b97-afa4996ea1eb
url    : https://smith.langchain.com/o/.../r/becf1436-...?poll=true
PII masking: OK — phone & email redacted
metadata.session_id=smoke-thread turn_id=9fdfae5b... intent=None
```
Pass criteria: **`PII masking: OK`**, a non-empty `url`, and `session_id` + `turn_id`
populated. (`intent=None` is correct here — a refusal never reaches the supervisor.)

### 8.2 Confirm in the LangSmith UI
Open the printed `url` (or go to `smith.langchain.com` → project **`fdm-agentic`**) and check:
- **Run tree** — the trace shows `input → output` as nested runs (for a real
  troubleshoot turn you'd see `input → supervisor → intake → diagnosis → verifier → …`,
  with each LLM/MCP-tool call as a leaf).
- **No PII** — open the root run's *Inputs*; the planted number/email read
  `[redacted-phone]` / `[redacted-email]`.
- **Metadata** — the run's *Metadata* tab shows `session_id`, `turn_id`, `user_id`,
  `models`, and (after `enrich_run`) `intent`, `needs_technician`, `verdict_score`, etc.
- **Latency / tokens / cost** — populated on each run.

### 8.3 Confirm turn + thread grouping (multi-turn)
```bash
python mcp_server/server.py http      # separate terminal (troubleshoot needs tools)
python agents/run.py
```
Have a short conversation that includes a clarification (so one turn spans a start + a resume). Then in LangSmith open **Threads**:
- all turns of that session group under one **`thread_id`**;
- the clarification turn's start + resume share one **`turn_id`** (labelled `turn:start` / `turn:resume`) — this is how separate turns are told apart.

### 8.4 Troubleshooting
| Symptom | Likely cause / fix |
|---|---|
| Smoke test prints `LANGSMITH_TRACING is not 'true'` | `LANGSMITH_TRACING=true` missing/typo in `.env` |
| `url` empty / `Could not read the run back` | bad/expired `LANGSMITH_API_KEY`, or wrong region — set `LANGSMITH_ENDPOINT` (EU: `https://eu.api.smith.langchain.com`) |
| No traces in the UI | you ran a path that bypasses `api.py` (e.g. direct `app_graph.ainvoke`) — only turns through `start_turn`/`resume_turn` are traced (see the design note below) |
| Each turn appears **twice** | the env auto-tracer wasn't disabled — ensure `observability` is imported before any invoke (it is, via `api.py`) |
| `PII masking: LEAK` | the redactor regex didn't match — check `_EMAIL_RE` / `_PHONE_RE` in `tracing.py` |

### Design note — why we attach our own tracer
Importing this module **disables the env auto-tracer** and attaches an explicit, PII-masking tracer instead. If both were active, every run would upload twice — once unmasked. So all traced turns must go through `make_config()` (which `api.py` always does). Direct `app_graph.ainvoke(...)` calls that bypass `api.py` (e.g. parts of `agents/test_e2e.py`) are simply not traced — which is fine.

---

## 9. Files

| File | Purpose |
|---|---|
| `tracing.py` | the layer: `make_config`, `enrich_run`, `new_turn_id`, `tracing_on`, `get_client`, PII redactor, masking tracer |
| `__init__.py` | public exports |
| `trace_smoke.py` | one-shot verification: a traced turn → run URL + PII-mask assertion |
| `README.md` | this document |

Consumed by `agents/api.py` (`start_turn` / `resume_turn`).

---

## 10. What's next

- **Evaluation, in `eval/`:** golden datasets + RAG groundedness (faithfulness, context precision/recall), retrieval metrics + reranker tuning, routing/SQL/structured-output validation, input-guard red-team + PII-leak checks, and a CI regression gate. The eval **judge** runs on a *separate* free provider (**OpenRouter + DeepSeek**, `OPENROUTER_API_KEY`) so it never competes with the app's Groq/Gemini quota — and many metrics (retrieval, SQL, routing, PII scan) need no LLM at all.

> Security: the LangSmith API key is a secret — `.env` only, never committed. If a key is ever exposed (e.g. pasted in chat), rotate it in LangSmith → Settings → API Keys.
