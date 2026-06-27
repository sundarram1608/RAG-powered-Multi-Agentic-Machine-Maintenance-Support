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

# graph node -> user-facing progress label (Phase 6b live streaming). Nodes without a
# label (e.g. "__interrupt__") leave the status text unchanged.
_NODE_LABELS = {
    "input": "Checking your request…",
    "supervisor": "Figuring out what you need…",
    "intake": "Identifying the machine…",
    "diagnosis": "Diagnosing the fault…",
    "verifier": "Double-checking the diagnosis…",
    "decider": "Preparing your options…",
    "self_action": "Preparing your options…",
    "technician_action": "Booking a technician…",
    "analytics_generate": "Writing a query…",
    "text_to_sql_reviewer": "Reviewing the query…",
    "analytics_execute": "Fetching the data…",
    "manage_resolve": "Looking up the incident…",
    "manage_execute": "Applying the change…",
    "output": "Writing the response…",
}


def _run_streamed(stream) -> dict:
    """Drive a backend stream: update a live status per node, return the result event."""
    result = None
    with st.status("Working…", expanded=False) as status:
        for ev in stream:
            if ev.get("type") == "progress":
                label = _NODE_LABELS.get(ev.get("node"))
                if label:
                    status.update(label=label)
            elif ev.get("type") == "result":
                result = ev
        status.update(label="Done", state="complete")
    return result or {"kind": "error",
                      "content": "⚠️ Sorry — something went wrong. Please try again."}


def init_session_state() -> None:
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("thread_id", uuid.uuid4().hex)
    st.session_state.setdefault("pending", None)   # active interrupt: {kind,payload,turn_id,run_id}
    st.session_state.setdefault("user_id", None)


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
            st.markdown(m["content"])


def _append(role, content) -> None:
    st.session_state.messages.append({"role": role, "content": content})


def _prompt_text(kind, payload) -> str:
    if kind == "decision":
        return payload.get("question") or "Would you like to fix it yourself, or book a technician?"
    if kind == "choice":
        return payload.get("guidance") or "How would you like to proceed?"
    if kind == "approve":
        return f"Please review and approve:\n\n> {payload.get('summary') or '(no summary)'}"
    return payload.get("question") or "Could you clarify that?"   # clarify


def _apply(res) -> None:
    """Update state from a backend result: final answer, a new interrupt, or an error."""
    if res["kind"] == "error":
        # provider/other failure (e.g. rate limit) — show the friendly message and
        # leave `pending` unchanged so the user can retry the same step.
        _append(ROLE_ASSISTANT, res.get("content") or "⚠️ Something went wrong. Please try again.")
        return
    if res["kind"] == "answer":
        _append(ROLE_ASSISTANT, res.get("content") or "_(no response)_")
        st.session_state.pending = None
    else:
        st.session_state.pending = {"kind": res["kind"], "payload": res.get("payload", {}),
                                    "turn_id": res.get("turn_id"), "run_id": res.get("run_id")}
        _append(ROLE_ASSISTANT, _prompt_text(res["kind"], res.get("payload", {})))


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
