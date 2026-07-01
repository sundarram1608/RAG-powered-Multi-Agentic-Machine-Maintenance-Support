"""
history.py — short-term conversation memory helper.

The graph keeps an append-only `messages` list (State.messages, add_messages
reducer): the user turn is appended at the start of a request (api.start_turn) and
the assistant's final reply at the end (output_node). This helper renders the most
recent exchanges as plain text so the front gates (Input guard, Supervisor) and the
Analytics coder can resolve brief follow-ups like "which are mine?" or "what about
the closed ones?" — turns that are meaningless in isolation.

Kept deliberately small (a fixed window, not the whole transcript): the structured
state is still the primary working memory; this is only enough context to interpret
a follow-up, and it keeps the per-call token cost bounded.
"""

from langchain_core.messages import HumanMessage


def format_recent(messages, max_exchanges: int = 5) -> str:
    """Render the last `max_exchanges` user/assistant pairs as labeled lines.

    Returns "" when there is nothing to show. Pass `messages[:-1]` when the current
    turn has already been appended and you want only the PRIOR context.
    """
    msgs = messages or []
    recent = msgs[-(2 * max_exchanges):]
    lines = []
    for m in recent:
        content = (getattr(m, "content", "") or "").strip()
        if not content:
            continue
        role = "User" if isinstance(m, HumanMessage) else "Assistant"
        lines.append(f"{role}: {content}")
    return "\n".join(lines)
