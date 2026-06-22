"""
Streamlit entrypoint for the Preventive Maintenance Assistant.

Run with:
    streamlit run app/main.py

This is a skeleton. The chat UI is wired up here; the agentic backend
(LangGraph graph) gets plugged in later via app_utils.
"""

import streamlit as st

from app_utils import init_session_state, render_chat_history, handle_user_message


def main() -> None:
    st.set_page_config(
                        page_title="Preventive Maintenance Assistant",
                        page_icon="🛠️",
                        layout="wide",
                    )

    st.title("🛠️ Agentic Preventive Maintenance")
    intro_text = f"""Hi, I'm your AI Preventive Maintenance Engineer.
                    \nI can help you troubleshoot and maintain your equipment.
                    \nI will be able to answer questions about your equipment and help you with your issues.
                    """
    st.info(intro_text)
    # Initialise per-session state (chat history, thread id, etc.)
    init_session_state()

    # Render the conversation so far.
    render_chat_history()

    # Chat input — accepts text and/or an image attachment (for the vision agent).
    submission = st.chat_input(
        "Describe the issue, or attach a photo of the defect…",
        accept_file=True,
        file_type=["png", "jpg", "jpeg"],
    )
    if submission:
        # `submission` is a ChatInputValue: .text (str) and .files (list of UploadedFile).
        handle_user_message(submission.text, submission.files)


if __name__ == "__main__":
    main()
