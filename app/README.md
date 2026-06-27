# App layer — Streamlit UI (Phase 6)

The operator-facing chat for the FDM maintenance assistant. It talks **only** to the
agent boundary (`agents/api.py`) — it never touches the graph, tools, or DB directly.

> Status: **6a ✅** — text chat + sidebar login + human-in-the-loop interrupts.
> **Next:** 6b live streaming progress, 6c 👍/👎 feedback. (Image/vision input removed.)

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

## Files
| File | Purpose |
|---|---|
| `main.py` | entrypoint: page config, sidebar login, history, interrupt controls, chat input |
| `app_utils.py` | session state, history rendering, the turn/interrupt dispatch, interrupt buttons |
| `backend.py` | async bridge (persistent loop) + thin wrappers over `agents/api.py`; `list_operators()` |
| `README.md` | this document |

## Verify (6a)
Pick an operator, then: **refusal** ("what's the capital of France?"), **general**
("what can you do?"), **analytics** ("how many incidents are open?") — no interrupts;
then a **troubleshoot** ("M03 prints aren't sticking") → `decision` → `choice` buttons,
and a **manage** ("close incident inc_22, replaced the thermistor") → `approve`.

## What's next
- **6b** — `stream_turn`/`stream_resume` in `api.py` + per-node live progress.
- **6c** — 👍/👎 feedback → `observability.log_feedback(run_id, …)` (the API already
  returns `run_id`).
