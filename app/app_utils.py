"""
app_utils.py — Streamlit UI helpers for the FDM maintenance assistant.

Session state, chat-history rendering, and the turn/interrupt dispatch that bridges the
UI to the agent backend. Text-only (no image/vision). Human-in-the-loop interrupts are
surfaced as a clarification prompt (typed answer) or as buttons (decision / choice /
approve), then resumed via backend.resume_turn.

Each message: {"role": "user"|"assistant", "content": str}.
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st

import backend

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"

# button-interrupts -> (label, resume-value) pairs
_BUTTONS = {
    "decision": [("🔧 I'll fix it myself", "self"), ("👷 Book a technician", "technician")],
    "choice": [("✅ Complete & close", "complete"), ("👷 Book a technician", "technician")],
    "approve": [("✅ Approve", "approve"), ("✖ Reject", "reject")],
}

def _run_streamed(stream) -> dict:
    """Drive a backend stream into a live activity log + a typing answer; return the
    final result event. Event types: decision/tool/step (log lines), code (a code block
    the agent wrote, e.g. generated SQL), token (answer tokens), result (the final
    answer/interrupt/error dict)."""
    result = None
    steps = []          # activity-log lines (decisions + tool calls)
    codes = []          # code blocks the agent wrote: {header, code, language}
    tokens = []         # streamed answer tokens
    status = st.status("Working…", expanded=True)   # the live log (on top; collapses at end)
    code_slot = st.empty()                           # the code expander (only the LATEST), below the log
    answer_box = st.empty()                          # the answer, BELOW that (stays visible)
    for ev in stream:
        t = ev.get("type")
        if t in ("decision", "tool", "step"):
            line = ev.get("text", "")
            if line:
                steps.append(line)
                status.update(label=line)               # spinner shows the latest step
                status.markdown("\n".join(f"- {s}" for s in steps))
        elif t == "code":
            block = {"header": ev.get("header", "") or "Query",
                     "code": ev.get("code", ""), "language": ev.get("language", "sql")}
            if block["code"]:
                codes.append(block)
                # Only the LATEST query is shown/kept — if execute retries on a DB error
                # and re-runs, this replaces the earlier block (final query = the answer).
                with code_slot.container():
                    with st.expander(f"🧮 {block['header']}", expanded=False):
                        st.code(block["code"], language=block["language"])
        elif t == "token":
            tokens.append(ev.get("text", ""))
            answer_box.markdown("".join(tokens))         # answer types out live
        elif t == "result":
            result = ev
    status.update(label="Done", state="complete", expanded=False)
    result = result or {"kind": "error",
                        "content": "⚠️ Sorry — something went wrong. Please try again."}
    result["steps"] = steps          # persist the activity feed with the message
    result["code_blocks"] = codes[-1:]   # persist ONLY the final query (the one behind the answer)
    return result


def init_session_state() -> None:
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("thread_id", uuid.uuid4().hex)
    st.session_state.setdefault("pending", None)   # active interrupt: {kind,payload,turn_id,run_id}
    st.session_state.setdefault("user_id", None)
    st.session_state.setdefault("feedback", {})     # run_id -> submitted score (6c thumbs)


def reset_conversation() -> None:
    st.session_state.messages = []
    st.session_state.thread_id = uuid.uuid4().hex
    st.session_state.pending = None


@st.cache_data(show_spinner=False)
def operators():
    return backend.list_operators()


def set_operator(user_id) -> None:
    """Switch operator; a new operator starts a fresh conversation."""
    if user_id != st.session_state.user_id:
        st.session_state.user_id = user_id
        reset_conversation()


def render_chat_history() -> None:
    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            steps = m.get("steps")
            if steps:   # the live activity feed, persisted as a collapsed expander above the reply
                with st.expander(f"🔎 Activity · {len(steps)} steps", expanded=False):
                    st.markdown("\n".join(f"- {s}" for s in steps))
            for block in m.get("code_blocks") or []:   # code the agent wrote (e.g. SQL)
                with st.expander(f"🧮 {block.get('header') or 'Query'}", expanded=False):
                    st.code(block.get("code", ""), language=block.get("language", "sql"))
            st.markdown(m["content"])
            if m.get("run_id"):
                _feedback_widget(m["run_id"])


def _append(role, content, steps=None, run_id=None, code_blocks=None) -> None:
    msg = {"role": role, "content": content}
    if steps:
        msg["steps"] = steps
    if code_blocks:         # code the agent wrote (e.g. generated SQL) -> its own expander
        msg["code_blocks"] = code_blocks
    if run_id:              # only answered turns carry a run_id -> feedback thumbs (6c)
        msg["run_id"] = run_id
    st.session_state.messages.append(msg)


def _feedback_widget(run_id: str) -> None:
    """👍/👎 under an answer → observability.log_feedback (6c). Rated once per run."""
    sel = st.feedback("thumbs", key=f"fb_{run_id}")   # 0 = 👎, 1 = 👍
    if sel is not None and st.session_state.feedback.get(run_id) != sel:
        backend.log_feedback(run_id, sel)
        st.session_state.feedback[run_id] = sel
    if run_id in st.session_state.feedback:
        st.caption("Thanks for the feedback.")


def _prompt_text(kind, payload) -> str:
    if kind == "decision":
        return payload.get("question") or "Would you like to fix it yourself, or book a technician?"
    if kind == "choice":
        return payload.get("guidance") or "How would you like to proceed?"
    if kind == "approve":
        return f"Please review and approve:\n\n> {payload.get('summary') or '(no summary)'}"
    return payload.get("question") or "Could you clarify that?"   # clarify


def _apply(res) -> None:
    """Update state from a backend result: final answer, a new interrupt, or an error.
    `res["steps"]` (the streamed activity feed) is persisted with the message so it stays
    visible as an expander in the history."""
    steps = res.get("steps")
    codes = res.get("code_blocks")
    if res["kind"] == "error":
        # provider/other failure (e.g. rate limit) — show the friendly message and
        # leave `pending` unchanged so the user can retry the same step.
        _append(ROLE_ASSISTANT, res.get("content") or "⚠️ Something went wrong. Please try again.",
                steps, code_blocks=codes)
        return
    if res["kind"] == "answer":
        _append(ROLE_ASSISTANT, res.get("content") or "_(no response)_", steps,
                run_id=res.get("run_id"), code_blocks=codes)   # answered turn -> feedback thumbs
        st.session_state.pending = None
    else:
        st.session_state.pending = {"kind": res["kind"], "payload": res.get("payload", {}),
                                    "turn_id": res.get("turn_id"), "run_id": res.get("run_id")}
        _append(ROLE_ASSISTANT, _prompt_text(res["kind"], res.get("payload", {})), steps,
                code_blocks=codes)


def handle_user_message(text) -> None:
    """A typed message: resume a clarification, or start a fresh turn. Rendered live;
    persisted to history for subsequent reruns."""
    text = (text or "").strip()
    if not text:
        return
    pending = st.session_state.pending
    _append(ROLE_USER, text)
    with st.chat_message(ROLE_USER):
        st.markdown(text)
    with st.chat_message(ROLE_ASSISTANT):
        if pending and pending["kind"] == "clarify":
            stream = backend.stream_resume(st.session_state.thread_id, text,
                                           pending["turn_id"], st.session_state.user_id)
        else:
            stream = backend.stream_turn(st.session_state.thread_id, st.session_state.user_id, text)
        res = _run_streamed(stream)
    _apply(res)
    # Re-render so the next interrupt's controls / chat-input placeholder reflect the
    # updated state immediately (otherwise they lag one run -> "submit twice").
    st.rerun()


def render_pending_controls() -> None:
    """When an interrupt is pending: render its action buttons (decision/choice/approve)
    and a Cancel escape hatch (for any interrupt, incl. a typed clarify)."""
    pending = st.session_state.pending
    if not pending:
        return
    if pending["kind"] in _BUTTONS:
        cols = st.columns(len(_BUTTONS[pending["kind"]]))
        for col, (label, value) in zip(cols, _BUTTONS[pending["kind"]]):
            if col.button(label, key=f"{pending['kind']}:{value}:{pending['turn_id']}", use_container_width=True):
                _append(ROLE_USER, label)
                with st.chat_message(ROLE_ASSISTANT):
                    res = _run_streamed(backend.stream_resume(
                        st.session_state.thread_id, value, pending["turn_id"], st.session_state.user_id))
                _apply(res)
                st.rerun()
    # escape hatch — abandon the pending interrupt and ask something else
    if st.button("✖ Cancel / ask something else", key=f"cancel:{pending['turn_id']}"):
        st.session_state.pending = None
        st.session_state.thread_id = uuid.uuid4().hex   # fresh graph state; orphan the paused turn
        _append(ROLE_ASSISTANT, "Okay — cancelled. What would you like to do instead?")
        st.rerun()
