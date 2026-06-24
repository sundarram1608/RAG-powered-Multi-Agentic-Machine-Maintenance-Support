# Observability layer (LangSmith) — Phase 5a

This layer makes every run of the agent workflow **observable**: each turn is
captured as a structured trace in [LangSmith](https://smith.langchain.com), with
PII masked, organised by conversation and turn, and annotated with the turn's
outcome. It is **backstage** — for *developers*, not end users.

> Status: **Phase 5a (tracing) ✅**. Datasets + evaluators (5b–5e) land in `eval/`.

---

## 1. Backstage vs frontstage — two different things

There are two ways to "see what the agents are doing", and they are **separate
mechanisms** that happen to read the same graph run:

| | Backstage (this layer) | Frontstage (Phase 6 app) |
|---|---|---|
| **Audience** | developers / operators of the system | end users (operator / technician / supervisor) |
| **Where** | LangSmith web UI (`smith.langchain.com`) | the Streamlit chat app |
| **Shows** | full run tree, latency, tokens, cost, errors, metadata | "thinking… calling tools… answer" progress + the final reply |
| **How** | LangChain tracer → LangSmith (this folder) | LangGraph `astream` events → UI (built in Phase 6) |
| **PII** | masked before storage (devs read it) | already scrubbed by the Output node |

**End users never see LangSmith.** It is an internal observability tool. The
"thinking / calling tools" effect you see in ChatGPT/Claude is the *frontstage*
piece — driven by `app_graph.astream(...)` and rendered by the app — and is **not**
part of this layer. (Designed, built in Phase 6 as `stream_turn`.)

---

## 2. What gets traced — the run tree

When tracing is on, **one invoke = one trace** (a "run tree"):

```
root run            = one invoke (a start_turn, or a single resume_turn)   START → END
├─ child run        = each graph node (input, supervisor, diagnosis, …)
│   └─ leaf run      = each LLM call / MCP tool call inside that node
└─ …
```

We do **not** create spans by hand. LangGraph's tracer builds the whole tree
automatically once a tracer is attached; this module only *attaches* the tracer,
*tags* the runs, and *masks* their contents.

> LangSmith calls these "runs"; they are the equivalent of OpenTelemetry "spans".

---

## 3. The three ids — and how turns are told apart

A conversation can contain many turns, and one turn can span several traces (a
clarification pauses the graph and each resume is a separate invoke). Three ids
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

So: `thread_id` answers "which conversation?", `turn_id` answers "which turn?", and
the trace is "one step of that turn".

---

## 4. Metadata attached to every trace

**At creation** (`make_config`): `session_id`, `thread_id`, `turn_id`, `user_id`,
and the `models` in use (reasoning / judge / judge_fallback) + tag `fdm-agentic`.

**After the turn** (`enrich_run` → `client.update_run`): the **outcome**, so you can
filter on it — `intent`, `machine_id`, `decision_path`, `needs_technician`,
`verdict_score`, `verdict_approved`, `verifier_exhausted`, `action`,
`prompt_versions`.

This is what lets you ask LangSmith things like *"all troubleshoot turns for M01
where the verifier rejected"* or *"every escalation to a technician this week"*.
(Per-node detail — corrective-RAG re-queries, whether the Qwen judge fallback fired
— is visible inside the run tree's child spans.)

---

## 5. PII masking

Developers read trace contents, so PII is masked **before** anything is uploaded:
a recursive redactor (`_redact`) strips emails and 7+‑digit phone numbers from all
run inputs/outputs, wired into the LangSmith client via `hide_inputs` / `hide_outputs`.

This is defense-in-depth on top of the existing guarantees:
1. the DB tools never return `phone` (stripped at source),
2. the Output node scrubs PII from the final reply,
3. **this layer** masks anything that slips through — e.g. a phone/email a *user*
   types into their message. `trace_smoke.py` proves it by planting a phone+email in
   the input and asserting they're redacted in the stored run.

---

## 6. How it ties to the app (data flow)

The UI never talks to LangSmith. Tracing is process-level; whoever calls the graph
(CLI, tests, or the app) is traced. The UI's only role is to supply the ids + message.

```
Operator types in the UI (Phase 6)
  └─ app calls  api.start_turn(thread_id, user_id, message)         # or resume_turn(..., turn_id)
       └─ observability.make_config → {thread_id, turn_id, user_id, run_id, metadata, tags, masking tracer}
            └─ app_graph.ainvoke(input, config)                      (Phase 6: astream)
                 ├─ FRONTSTAGE: astream events → UI progress + reply          (Phase 6)
                 └─ BACKSTAGE: tracer auto-captures the run tree → LangSmith  (this layer)
       └─ observability.enrich_run(run_id, metadata, final_state)    # outcome on the trace
```

`thread_id` comes from the chat session, `user_id` from login, `turn_id` is minted
per request (reused while paused). The `observability/` package sits **beside**
`agents/api.py`, not inside the UI.

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
3. **Verify**:
   ```bash
   python observability/trace_smoke.py
   ```
   Prints the run URL and asserts PII was masked. Open the link → you should see the
   run tree, the metadata, and (after a few turns) the Threads grouping.

Tracing is **gated by `LANGSMITH_TRACING`** — set it to anything but `true` and this
layer becomes a no-op (the app runs identically, untraced). No node code depends on it.

### Design note — why we attach our own tracer
Importing this module **disables the env auto-tracer** and attaches an explicit,
PII-masking tracer instead. If both were active, every run would upload twice — once
unmasked. So all traced turns must go through `make_config()` (which `api.py` always
does). Direct `app_graph.ainvoke(...)` calls that bypass `api.py` (e.g. parts of
`agents/test_e2e.py`) are simply not traced — which is fine.

---

## 8. Files

| File | Purpose |
|---|---|
| `tracing.py` | the layer: `make_config`, `enrich_run`, `new_turn_id`, `tracing_on`, `get_client`, PII redactor, masking tracer |
| `__init__.py` | public exports |
| `trace_smoke.py` | one-shot verification: a traced turn → run URL + PII-mask assertion |
| `README.md` | this document |

Consumed by `agents/api.py` (`start_turn` / `resume_turn`).

---

## 9. What's next

- **Frontstage progress (Phase 6):** `stream_turn` over `app_graph.astream` →
  per-node status + token streaming in the Streamlit UI.
- **Evaluation (Phase 5b–5e), in `eval/`:** golden datasets + RAG groundedness
  (faithfulness, context precision/recall), retrieval metrics + reranker tuning,
  routing/SQL/structured-output validation, input-guard red-team + PII-leak checks,
  and a CI regression gate. The eval **judge** runs on a *separate* free provider
  (**OpenRouter + DeepSeek**, `OPENROUTER_API_KEY`) so it never competes with the
  app's Groq/Gemini quota — and many metrics (retrieval, SQL, routing, PII scan) need
  no LLM at all.

> Security: the LangSmith API key is a secret — `.env` only, never committed. If a key
> is ever exposed (e.g. pasted in chat), rotate it in LangSmith → Settings → API Keys.
