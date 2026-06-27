# App layer — Streamlit UI (Phase 6)

The operator-facing chat for the FDM maintenance assistant. It talks **only** to the
agent boundary (`agents/api.py`) — it never touches the graph, tools, or DB directly.

> Status: **6a ✅** text chat + login + interrupts · **6b ✅** live per-node progress.
> **Next:** 6c 👍/👎 feedback. (Image/vision input removed.)

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

## Live progress (6b)
Turns are **streamed**: `api.stream_turn` / `stream_resume` run the graph with
`astream(stream_mode="updates")` and yield `{"type":"progress","node":…}` per node, then
a final `{"type":"result", …}` (the same answer/interrupt/error dict the non-streaming
calls return). `backend.stream_*` bridges that async generator to the sync UI via a
thread-safe queue. `app_utils._run_streamed` drives a collapsible `st.status` whose label
updates per node (`_NODE_LABELS`, e.g. "Diagnosing the fault…"), then `_apply` handles the
result exactly as before. Interrupts still pause (the stream ends at `__interrupt__`); the
non-streaming `start_turn`/`resume_turn` remain for any non-UI caller.

## Files
| File | Purpose |
|---|---|
| `main.py` | entrypoint: page config, sidebar login, history, interrupt controls, chat input |
| `app_utils.py` | session state, history rendering, the turn/interrupt dispatch, interrupt buttons |
| `backend.py` | async bridge (persistent loop) + wrappers over `agents/api.py` (`start_turn`/`resume_turn` + streaming `stream_turn`/`stream_resume`); `list_operators()` |
| `README.md` | this document |

## Verify (6a + 6b)
Pick an operator, then: **refusal** ("what's the capital of France?"), **general**
("what can you do?"), **analytics** ("how many incidents are open?") — no interrupts;
then a **troubleshoot** ("M03 prints aren't sticking") → `decision` → `choice` buttons,
and a **manage** ("close incident inc_22, replaced the thermistor") → `approve`.
For **6b**, watch the `st.status` step labels advance during a turn (e.g. analytics:
"Writing a query…" → "Reviewing the query…" → "Fetching the data…" → "Writing the
response…"). *(Needs Groq daily budget available — a rate-limited turn streams the
friendly cap message instead.)*

## What's next
- **6c** — 👍/👎 feedback → `observability.log_feedback(run_id, …)` (the API already
  returns `run_id`).
