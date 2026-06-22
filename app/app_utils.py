"""
Helper functions for the Streamlit app.

Keeps main.py thin: session-state setup, chat rendering, and the bridge
between the UI and the agentic backend live here. The backend call is a
stub for now and will be replaced by the LangGraph graph invocation.

Messages support text and/or image attachments. Each message is:
    {
        "role": ROLE_USER | ROLE_ASSISTANT,
        "content": str,            # may be empty if only an image was sent
        "images": list[bytes],     # raw image bytes, for rendering + the vision agent
    }
"""

from typing import List, Optional

import streamlit as st

# Roles used in the chat history.
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"


def init_session_state() -> None:
    """Initialise session-scoped state on first load."""
    if "messages" not in st.session_state:
        st.session_state.messages = []

    if "thread_id" not in st.session_state:
        # One conversation thread per Streamlit session. Used later as the
        # LangGraph checkpointer key so clarification turns share state.
        st.session_state.thread_id = _new_thread_id()


def render_chat_history() -> None:
    """Render all messages stored in session state."""
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            _render_message(message)


def handle_user_message(user_text: str, uploaded_files: Optional[List] = None) -> None:
    """Append the user's message (text and/or images), get a reply, render both."""
    # Read uploaded images into raw bytes so they survive reruns
    # (UploadedFile streams are consumed once).
    images = [f.getvalue() for f in (uploaded_files or [])]

    # Ignore empty submissions (no text and no image).
    if not user_text and not images:
        return

    user_message = {"role": ROLE_USER, "content": user_text, "images": images}
    st.session_state.messages.append(user_message)
    with st.chat_message(ROLE_USER):
        _render_message(user_message)

    # Get the assistant reply (stubbed for now).
    with st.chat_message(ROLE_ASSISTANT):
        with st.spinner("Thinking…"):
            reply = run_agent(user_text, images)
        st.markdown(reply)

    st.session_state.messages.append(
        {"role": ROLE_ASSISTANT, "content": reply, "images": []}
    )


def run_agent(user_text: str, images: Optional[List] = None) -> str:
    """
    Bridge to the agentic backend.

    STUB: echoes the input. This will be replaced by a call into the
    LangGraph graph (passing text + images + thread_id and streaming the
    supervisor's response back). Images route to the vision agent.
    """
    image_note = (
        f"\n\n🖼️ Received **{len(images)}** image(s) — these will go to the "
        "vision agent once it's wired up."
        if images
        else ""
    )
    return (
        "🧪 _Backend not wired up yet._\n\n"
        f"You said: **{user_text or '(no text)'}**"
        f"{image_note}\n\n"
        "Once the LangGraph graph is connected, this will run the "
        "input-guard → (vision) → diagnosis → tools flow and return a real answer."
    )


def _render_message(message: dict) -> None:
    """Render a single message's text and any attached images."""
    if message.get("content"):
        st.markdown(message["content"])
    for image_bytes in message.get("images", []):
        st.image(image_bytes, width=300)


def _new_thread_id() -> str:
    """Generate a unique conversation thread id."""
    import uuid

    return uuid.uuid4().hex
