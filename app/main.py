"""
Streamlit entrypoint for the Agentic FDM Maintenance assistant (Phase 6a).

Text-only chat over the LangGraph workflow (agents/api.py via app/backend.py), with a
sidebar operator login and human-in-the-loop interrupts (clarify / decision / choice /
approve). Streaming progress (6b) and feedback buttons (6c) come next.

Run (HTTP MCP server must be up):
    python mcp_server/server.py http        # separate terminal
    streamlit run app/main.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st

from app_utils import (
    handle_user_message,
    init_session_state,
    operators,
    render_chat_history,
    render_pending_controls,
    reset_conversation,
    set_operator,
)


def main() -> None:
    st.set_page_config(page_title="Agentic FDM Maintenance", page_icon="🛠️", layout="centered")
    st.title("🛠️ Agentic FDM Maintenance")
    init_session_state()

    # --- sidebar: operator login + new conversation ---
    with st.sidebar:
        st.header("Operator")
        ops = operators()
        labels = [lbl for _, lbl in ops]
        ids = [oid for oid, _ in ops]
        choice = st.selectbox("Logged in as", labels, index=None,
                              placeholder="Pick an operator", key="op_choice")
        set_operator(ids[labels.index(choice)] if choice else None)
        if st.button("🔄 New conversation", use_container_width=True):
            reset_conversation()
            st.rerun()

    if not st.session_state.user_id:
        st.info("👈 Pick an operator in the sidebar to start.")
        st.stop()

    st.caption("Ask about a machine fault, maintenance data, or an incident.")
    render_chat_history()
    render_pending_controls()

    pending = st.session_state.pending
    button_interrupt = bool(pending and pending["kind"] != "clarify")
    # The clarify question is already shown in the chat bubble above — keep the input
    # placeholder short (don't echo the whole question, which can be long guidance).
    placeholder = ("Type your answer…" if (pending and pending["kind"] == "clarify")
                   else "Describe the issue, ask a question, or manage an incident…")
    prompt = st.chat_input(placeholder, disabled=button_interrupt)
    if prompt:
        handle_user_message(prompt)


if __name__ == "__main__":
    main()
