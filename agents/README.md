# Agent layer (LangGraph)

The **brain** of the system. A multi-agent workflow built with **LangGraph** that
orchestrates the **MCP tools** (the "hands") and the **Knowledge layer** (DB + RAG)
to troubleshoot FDM 3D-printer faults, answer analytics questions, and take
actions (open incidents, book technicians, notify people) — with verification and
human-in-the-loop before anything irreversible.

> Build status: **Phase 4a (foundations) ✅** · nodes & graph in progress.
> The workflow **graph diagram** will be generated from the compiled graph at the
> end of Phase 4 and embedded here. *(placeholder — to be added.)*

---

## Design philosophy

- **Supervisor-orchestrated, single-responsibility agents.** Small agents with
  small tool sets → better tool selection, cheaper prompts, isolated failures,
  easier to test and observe.
- **Deterministic edges where possible; LLM routing only where a real decision
  exists** (intent routing, self-fix vs technician). Predictable + cheap.
- **Structured outputs (Pydantic).** Every reasoning node returns a validated
  object (`schemas.py`); the graph routes on typed fields, not parsed text.
- **Tools are the only data access.** Nodes never touch MySQL/Chroma directly —
  they call the MCP tools, inheriting their safety/PII guarantees.
- **Independent verification.** The Verifier uses a *different* model family
  (Gemini) than the reasoner (Groq Llama) to avoid correlated blind spots.
- **Human-in-the-loop before irreversible actions** (writes, emails) via
  LangGraph `interrupt()`.
- **Least privilege for agents**, mirroring the DB users: each agent is bound to
  ONLY the tools it needs (see the allow-list below).
- **Everything free** — Groq + Gemini free tiers; BGE-M3 local embeddings.

---

## The agents & workflow

| # | Agent | LLM | Tools | Role |
|---|---|---|---|---|
| 1 | **Input** | Llama | — | scope + prompt-injection / PII-request guard |
| 2 | **Supervisor** | Llama | — | route: troubleshoot / analytics / manage_incident / general |
| 3 | **Analytics** | Llama | `run_readonly_query` | coder: NL → read-only SQL; executor: run approved SQL |
| 4 | **Text-to-SQL Reviewer** | **Gemini** | — | judge the SQL: grounded / relevant / safe; loop back if not |
| 5 | **Manage Incident** | Llama | write/booking tools *(TBD)* | direct action on a KNOWN incident/booking (close, reassign, (re)book) |
| 6 | **Intake** | Llama | `get_machine` | resolve & validate machine; clarify if needed |
| 7 | **Diagnosis** | Llama | RAG + DB read tools | gather evidence (corrective-RAG) → root cause + fix |
| 8 | **Verifier** | **Gemini** | — | judge groundedness/relevance/safety; loop back if weak |
| 9 | **Decider** | Llama | — | ask the user: self-fix or technician? |
| 10 | **Self Action** | Llama | RAG + `create_incident`, `update_incident` | guide the operator (with safety); log a self-resolved incident |
| 11 | **Technician Action** | Llama | `find_available_technician`, `create_incident`, `book_technician_slot`, `update_incident`, `send_email` | book a technician/supervisor, update tables, notify |
| 12 | **Output** | Llama | — | compose ALL final replies (+ mid-flow asks via interrupt); final PII scrub |

**Flow (narrative):** user turn → **Input** (scope/safety) → **Supervisor** routes →
*analytics* = **Analytics** (coder) → **Text-to-SQL Reviewer** → *(approved)*
**Analytics** (execute) → **Output**; *(reviewer-reject or DB-error loops back to
the coder, capped at `ANALYTICS_MAX_ATTEMPTS`)*. *manage_incident* = **Manage
Incident** (approval interrupt before writes) → **Output**; *general* = direct
**Output**; *troubleshoot* = **Intake** (clarify via interrupt if details missing)
→ **Diagnosis** (RAG + DB) → **Verifier** (retry loop, capped at
`VERIFY_MAX_ATTEMPTS`) → **Decider** (asks the user) → **Self Action** *or*
**Technician Action** (approval interrupt before writes/email) → **Output**.

---

## How it connects to the app

The compiled graph is the **only** thing a front-end talks to, through a thin
boundary (built later):

```
start_turn(thread_id, user_id, message) -> Result
resume_turn(thread_id, value)           -> Result   # answer a clarification / approve an action
```
- `thread_id` = one chat (memory + pause/resume via the checkpointer).
- `user_id` = the logged-in operator's `employee_id` (drives `create_incident(reported_by=…)` and notifications — set from login, never asked in chat).
- `interrupt()` points (Intake clarify, Decider choice, Technician-Action approval) surface as `needs_input`/`needs_approval`; the app renders a prompt / Approve-Reject and calls `resume_turn`.
- **Now:** a CLI driver (`run.py`, later) calls these. **Phase 6:** Streamlit wraps the *same* functions — no graph changes.

## Memory & threads

- **Within a thread:** after each step LangGraph **checkpoints** the full `State`
  keyed by `thread_id`; the next turn reloads it → the conversation continues.
- **Across threads:** isolated — a new chat is a new `thread_id` with fresh state
  (no sharing). *(Optional cross-thread long-term memory via a Store is not used.)*
- **Long chats (e.g. 80 turns):** there is no fixed "thread token limit" — the
  checkpointer persists everything; the constraint is the **LLM context window**
  per call (Llama ≈128K, Gemini ≈1M). We keep calls small by (1) reading typed
  **state fields** instead of the raw transcript, (2) trimming/windowing messages,
  (3) summarizing older turns.

## LLM strategy

| Role | Model | Why |
|---|---|---|
| Reasoning | **Groq Llama 3.3 70B** | fast, strong tool-calling, free |
| Verifier / future vision | **Gemini 2.5 Flash** | independent family; multimodal; free |
| Embeddings (RAG) | **BGE-M3 (local)** | free, no rate limits, deterministic |

Switching providers is a one-line change in `llms.py`. Keys: `GROQ_API_KEY`,
`GOOGLE_API_KEY` in `.env` (both free).

---

## MCP connection & per-agent tool allow-list

The agents connect to **both** MCP servers at once via
`langchain-mcp-adapters`' `MultiServerMCPClient` (`mcp_client.py`):

- **stdio** (`local_data`) — auto-spawned; the 11 read/RAG/write tools.
- **streamable-HTTP** (`services`, `127.0.0.1:8000`) — separate process; `run_readonly_query`, `send_email`.

`get_all_tools()` returns the union (13 tools); `tools_for(agent, tools)` filters
to each agent's allow-list (`config.AGENT_TOOLS`):

| Agent | Tools |
|---|---|
| input · supervisor · text_to_sql_reviewer · verifier · decider · output | *(none)* |
| analytics | `run_readonly_query` |
| intake | `get_machine` |
| diagnosis | `user_manual_retrieval`, `safety_retrieval`, `get_overdue_status`, `get_maintenance_history`, `get_incident_history`, `check_inventory` |
| self_action | `user_manual_retrieval`, `safety_retrieval`, `create_incident`, `update_incident` |
| technician_action | `find_available_technician`, `create_incident`, `book_technician_slot`, `update_incident`, `send_email` |

**Launch order:** `python mcp_server/server.py http` (HTTP services server) → then
run the agent (it auto-spawns the stdio server and connects to the HTTP one).

---

## Phase 4a — Foundations  ✅

The plumbing every node stands on (no nodes yet):

| File | Purpose |
|---|---|
| [`config.py`](config.py) | models, MCP server map, **per-agent tool allow-lists**, workflow constants, API keys |
| [`schemas.py`](schemas.py) | Pydantic structured outputs (`GuardResult`, `Route`, `Intake`, `Diagnosis`, `Verdict`, `Decision`, `SqlAnswer`) |
| [`llms.py`](llms.py) | `get_reasoner()` (Groq) · `get_judge()` (Gemini) — provider factory |
| [`mcp_client.py`](mcp_client.py) | connect to both MCP servers; `get_all_tools()` + `tools_for(agent)` |

**Milestone test** (`python agents/mcp_client.py`, under a clearly-marked
`MILESTONE TEST` header):
- **Part 1 (no API key):** connect to both servers, list the 13 tools, print each agent's resolved allow-list.
- **Part 2 (needs `GROQ_API_KEY`):** bind `tools_for("intake")` to the reasoner and confirm it emits a `get_machine` tool call.

---

## Agents (filled in as each is built — Phase 4b)

> Build order: Input → Supervisor → Analytics → **Text-to-SQL Reviewer** →
> Manage Incident → Intake → Diagnosis → Verifier → Decider → Self Action →
> Technician Action → Output.
> Prompts are versioned in `prompts/<agent>.py` (a `VERSION` + changelog header);
> each run is tagged with the `prompt_version` it used. Prompt text is not
> reproduced here.
>
> **Every node** reads/writes the shared `State` and (for reasoning nodes) returns
> a **Pydantic** model via `llm.with_structured_output(Model)` — so each subsection
> states the **input format** (state keys read) and **output format** (the Pydantic
> model + state keys written).

### 1. Input Agent — `nodes/input.py`  ✅
- **Purpose:** the front gate — classify each user turn as **in-scope** (FDM maintenance/service/faults, analytics, capabilities, or operational incident/booking actions) **and safe** (no prompt-injection, no PII/credential extraction). Pure classifier; it never answers or acts.
- **LLM:** **Groq Llama 3.3 70B** (reasoner), `with_structured_output(GuardResult)`.
- **Tools:** none.
- **Input:** the current user turn (`state.user_input`, else the last message).
- **Output:** `{input_safe: bool, guard_reason: str, prompt_versions["input"]}`.
- **Routing:** `input_safe = False` → **Output** (polite refusal carrying `guard_reason`); `True` → **Supervisor**.
- **Edge cases:** instruction-override / "print your prompt" → `safe=False`; request for an employee's phone/email/credentials → `safe=False` (even when the topic is in-scope); off-domain question → `safe=False`; **operational actions** like "mark incident complete" / "book a technician" → `safe=True` (capability decided downstream); vague/ambiguous but on-topic → `safe=True` (clarified by later agents). **Moderate** strictness — only *clear* overrides/PII are blocked.
- **Prompt:** `prompts/input.py` · v1.0.0.

### 2. Supervisor Agent — `nodes/supervisor.py`  ✅
- **Purpose:** the intent router — classify the (already-guarded) turn into exactly one of four routes. Pure router; never answers or acts.
- **LLM:** **Groq Llama 3.3 70B** (reasoner).
- **Tools:** none.
- **Input format** (state keys read): `user_input` (else the last `messages` entry).
- **Output format** (Pydantic `Route` via `with_structured_output`) → writes state: `intent` (`"troubleshoot" | "analytics" | "manage_incident" | "general"`), `prompt_versions["supervisor"]`.
- **Routing:** `troubleshoot` → **Intake** · `analytics` → **Analytics** · `manage_incident` → **Manage Incident** · `general` → **Output**.
- **Edge cases:** READ data question → `analytics`, WRITE/action on a known record → `manage_incident`; a symptom that needs diagnosing → `troubleshoot` (even if "log it" is mentioned); capability/greeting → `general`; ambiguous-but-actionable → `troubleshoot` (Intake clarifies, avoiding dead-ends).
- **Prompt:** `prompts/supervisor.py` · v1.0.0.

### 3. Analytics Agent (Text-to-SQL coder + executor) — `nodes/analytics.py`  ✅
- **Purpose:** answer read-only analytics questions by generating SQL (grounded in the schema) and, after the Reviewer approves, executing it. Result summarization is the **Output** agent's job.
- **LLM:** **Groq Llama 3.3 70B** (generate phase); **no LLM** in the execute phase.
- **Tools:** `run_readonly_query` (execute phase only).
- **Two phases (one agent):** `analytics_generate` (LLM → `SqlPlan`) and `analytics_execute` (mechanical `run_readonly_query` → rows).
- **Input format** (state read): `user_input`; on retry also `sql_plan` + `sql_review`/`sql_result` (the critique/DB-error to fix).
- **Output format** (Pydantic `SqlPlan` via `with_structured_output`) → state `sql_plan`, `analytics_attempts`; execute → `sql_result`; tags `prompt_versions["analytics"]`.
- **Schema grounding:** the prompt is filled with `get_schema_context()` (from `schema_metadata.json`) + `REFERENCE_TODAY` (`2026-06-16`) so date logic matches the dataset.
- **Edge cases:** reviewer-reject or DB-error → regenerate with the critique (capped at `ANALYTICS_MAX_ATTEMPTS = 3`); never selects `phone`; empty result → handled by Output ("no matching records"); results auto-capped at 200 rows.
- **Prompt:** `prompts/analytics.py` (`ANALYTICS_CODER_SYSTEM`) · v1.0.0.

### 4. Text-to-SQL Reviewer — `nodes/text_to_sql_reviewer.py`  ✅
- **Purpose:** judge the generated SQL **before** it runs — the *semantic* layer of a 3-layer defense (reviewer = grounded/relevant/safe; `validate_select_sql` = mechanical; `maint_readonly` = DB enforcement).
- **LLM:** **Gemini 2.5 Flash** (independent judge — different model family than the Llama coder).
- **Tools:** none.
- **Input format** (state read): `user_input`, `sql_plan`.
- **Output format** (Pydantic `SqlReview` via `with_structured_output`) → state `sql_review` (`grounded`, `relevant`, `safe`, `approved`, `issues`); tags `prompt_versions["text_to_sql_reviewer"]`.
- **Loop:** `approved = grounded ∧ relevant ∧ safe`. If not approved (or execution later errors) → back to Analytics coder with `issues`, capped at `ANALYTICS_MAX_ATTEMPTS`; on exhaustion → Output (graceful "couldn't answer reliably").
- **Edge cases:** invented table/column → `grounded=False`; wrong computation for the question → `relevant=False`; write/`phone`/multi-statement → `safe=False`. Knows `REFERENCE_TODAY`, so it does **not** penalize correct use of the fixed reference date.
- **Prompt:** `prompts/text_to_sql_reviewer.py` · v1.0.0.

## Graph assembly (Phase 4c)

> `graph.py`: `StateGraph`, edges + conditional edges (clarification, verification
> retry, approval), checkpointer, `compile()`. The generated graph diagram will be
> embedded at the top of this README. *(to be added.)*

## Running it

> CLI driver + launch order. *(to be added.)*
