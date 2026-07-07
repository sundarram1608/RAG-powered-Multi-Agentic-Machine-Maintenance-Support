# App layer — Streamlit UI (Phase 6)

The operator-facing chat for the FDM maintenance assistant. It talks **only** to the
agent boundary (`agents/api.py`) — it never touches the graph or tools directly (the
one exception is `backend.list_operators()`, a read-only `SELECT` on employees to
populate the login dropdown; the agent path itself is untouched).


## Run
The HTTP MCP server must be up (the stdio tools server auto-spawns):
```bash
python mcp_server/server.py http      # terminal 1
streamlit run app/main.py             # terminal 2
```
Needs `GROQ_API_KEY` + `GOOGLE_API_KEY` in `.env` (plus `LANGSMITH_*` if tracing).

## How it works
1. **Login** — pick an operator in the sidebar (active employees from the DB). The
   chosen `employee_id` is the `user_id` passed to the backend (it drives
   `create_incident(reported_by=…)` + notifications). Switching operator starts a fresh
   conversation; "New conversation" resets the thread.
2. **Chat** — one Streamlit session = one `thread_id` (the LangGraph checkpointer key,
   so context + paused turns persist across reruns). Type a fault, a data question, or
   an incident instruction.
3. **Interrupts (human-in-the-loop)** — when the graph pauses, the UI surfaces it:

   | Interrupt | UI |
   |---|---|
   | `clarify` | the chat input becomes the answer box (placeholder = the question) |
   | `decision` | buttons: **🔧 I'll fix it myself** / **👷 Book a technician** |
   | `choice` | buttons: **✅ Complete & close** / **👷 Book a technician** |
   | `approve` | buttons: **✅ Approve** / **✖ Reject** |

   While a button-interrupt is pending, the chat input is disabled (choose an option).
   A **✖ Cancel / ask something else** button is shown for any pending interrupt — it
   abandons the paused turn (starts a fresh thread) so you can ask something different.
   For a `manage_incident` action with no incident id, the agent **lists the open
   incidents to pick from** (say "mine" for your own, "closed"/"all" to widen).
   If you answer a `clarify` question with "I don't know" / "I'm not sure", the agent
   doesn't just repeat it — it explains **how to get the info** (e.g. where the machine
   id is) and points to Cancel; after a few tries it stops with a clear message.
   Typing "ok" / "cancel" / "never mind", or a different request (e.g. "I want to open a
   new incident"), **stops the clarification** instead of re-asking — your next message
   then routes fresh.

## Async bridge
Streamlit is synchronous; the agent API is async, and the MCP client/graph must stay on
one event loop. `backend.py` runs a **single asyncio loop on a daemon thread** for the
app's lifetime; each UI call submits a coroutine to it and blocks for the result. The
`app_graph` + its `MemorySaver` are module-level, so a paused turn survives Streamlit
reruns and resumes correctly.

> **Editing the backend?** Because the asyncio loop, `app_graph` and `MemorySaver`
> are created **once at import**, Streamlit's file-watcher hot-reload (which re-runs
> the *script* but keeps already-imported modules cached) does **not** pick up edits
> to `backend.py`, `agents/`, or the prompts. Stop the process (`Ctrl+C`) and
> **fully restart** `streamlit run app/main.py` — a browser refresh or auto-rerun is
> not enough.

## Live progress (6b) — activity feed + streamed answer
Turns are **streamed** as a live activity feed, not static labels. `api.stream_turn` /
`stream_resume` run the graph with `astream(stream_mode=["updates","messages","custom"])`
and translate the three modes into events:
- **`decision`** (from `updates`) — a short line summarising each finished agent from its
  output fields: "🧭 Routing → analytics", "🔬 Diagnosis → thermistor fault (confidence
  medium)", "⚖️ Verifier → approved (4/5)". These `reason`/`evidence`/`verdict` fields are
  the closest thing the (structured-output) agents have to a visible thought process.
- **`tool`** (from `custom`) — each tool call a node makes, surfaced via
  [`agents/utils/streaming.py`](../agents/utils/streaming.py) `emit_tool()`: "🔧 Searching the manual",
  "🔧 Booking the technician · E13". (LangGraph's built-in streams can't see these — our
  tools are called directly through the MCP client — so nodes emit them explicitly.)
  Nodes can also emit a generic **`step`** line (via `streaming.emit`) for a non-tool
  progress note; the UI renders `decision` / `tool` / `step` identically as log lines.
- **`code`** (from `custom`, via `streaming.emit_code`) — a code block the agent *wrote*
  to fetch something (currently the Analytics coder's generated **SQL**). It renders as
  its **own collapsible `🧮 <header>` expander** (header = the query's rationale, i.e.
  what it's trying to find; body = the SQL via `st.code`), separate from the activity
  log, and is persisted with the message so it stays in scrollback.
- **`token`** (from `messages`, Output node only) — the final answer **types out live**;
  a trailing full-message repeat is de-duped in `api.py`.

`backend.stream_*` bridges the async generator to the sync UI via a thread-safe queue.
`app_utils._run_streamed` renders the decision/tool lines into a collapsible `st.status`
log (auto-expanded while running, collapses to "Done") with the answer streaming just
below it. The collected step lines are then **persisted with the message** (`steps` on the
history entry) and re-rendered as a collapsed **"🔎 Activity" expander above the reply**, so
the feed doesn't vanish after the turn — `render_chat_history` shows it for any past turn.
Interrupts still pause (the stream ends at `__interrupt__`); the non-streaming
`start_turn`/`resume_turn` remain for any non-UI caller.

> Note: the agents run on Groq Llama + Gemini via `with_structured_output`, which do **not**
> emit chain-of-thought — so this is a faithful *activity* feed (decisions + tools + the
> answer), not literal model "thinking" like Claude's extended-thinking view.

## Files
| File | Purpose |
|---|---|
| `main.py` | entrypoint: page config, sidebar login, history, interrupt controls, chat input |
| `app_utils.py` | session state, history rendering (incl. the activity expander + 👍/👎 thumbs), the turn/interrupt dispatch, interrupt buttons |
| `backend.py` | async bridge (persistent loop) + wrappers over `agents/api.py` (`start_turn`/`resume_turn` + streaming `stream_turn`/`stream_resume`); `log_feedback()`; `list_operators()` |
| `README.md` | this document |

## Verify (6a + 6b)
Pick an operator, then: **refusal** ("what's the capital of France?"), **general**
("what can you do?"), **analytics** ("how many incidents are open?") — no interrupts;
then a **troubleshoot** ("M03 prints aren't sticking") → `decision` → `choice` buttons,
and a **manage** ("close incident inc_22, replaced the thermistor") → `approve`.
Also try **advice** ("what should I do if the bed heats up too rapidly?") → a grounded,
safety-first answer with no machine asked; and an **ambiguous** one ("the bed is heating
rapidly") → a `clarify` ask "are you seeing this now, or asking?" — "yes, on M05" hands off
to troubleshooting, "just asking" answers.
For **6b**, watch the live activity log fill in during a turn (e.g. analytics: 🧭 Routing
→ analytics · 🧮 Wrote a SQL query · 🔎 SQL review → approved · 🔧 Querying the database)
with the answer then typing out below it. *(Needs Groq daily budget available — a
rate-limited turn streams the friendly cap message instead.)*

## Feedback (6c)
Each answered turn shows a **👍/👎** (`st.feedback("thumbs")`) below the reply. A click
calls `backend.log_feedback(run_id, score)` → `observability.log_feedback` (score 1=👍,
0=👎), attaching the rating to **that turn's LangSmith run** — which already carries the
full agent trace (intent, prompt versions, tools, verdict). A **👎 also auto-flags** the
run to the review queue (`flag_for_review`). `run_id` is persisted on the answer message
(so thumbs work in scrollback); votes are tracked in `st.session_state.feedback` to avoid
double-submit; no-ops silently if tracing is off. Only *answers* get thumbs (not
interrupts/errors). This closes the quality loop: 👎 runs are debuggable end-to-end,
attributable to a specific agent/prompt version, and can seed the eval/regression sets.
