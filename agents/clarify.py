"""
clarify.py — make clarification interrupts GUIDE a stuck user instead of re-asking.

When a free-text `clarify` interrupt (intake machine/symptom, manage comment) re-asks
and the user signals they don't know the answer, we acknowledge that, explain HOW to
obtain the information, repeat the ask, and point to the Cancel escape — rather than
looping the identical question. After the re-ask cap is hit, `give_up()` ends the
turn cleanly instead of proceeding with missing info.

Used by: nodes/intake.py, nodes/manage_incident.py, and the interrupt wrappers in
graph.py. Guidance is reactive (only shown once the user is stuck), so the first ask
stays short.
"""

import re

# "I don't know" / "not sure" / "can't find" / "where/how do I ..." / "idk" ...
# Note: the don't-patterns key off the literal "don't/dont/do not" — they deliberately
# do NOT match "doesn't"/"won't", so a symptom reply ("the fan doesn't spin") is safe.
_DONT = r"(?:do\s*n['’]?t|don['’]?t|dont|do\s+not)"
_STUCK_RE = re.compile(
    # "I don't know / have / remember / recall ..." (knowledge / possession sense)
    rf"\b{_DONT}\s+(?:know|knw|have|remember|recall|get|see)\b"
    # looser: "don't ... (the) machine / number / id / which" — catches the common
    # elliptical "I dont the machine number" (verb dropped)
    rf"|\b{_DONT}\b.{{0,24}}\b(?:machine|number|id|which one)\b"
    r"|not\s+sure|no\s+idea|no\s+clue"
    r"|can['’]?t\s+(?:find|remember|recall)"
    r"|haven['’]?t\s+got"
    r"|\bunsure\b|\bdunno\b|\bidk\b"
    r"|where\s+(?:do|can|is|to)|how\s+(?:do|can|to)",
    re.I,
)


def is_stuck(text: str) -> bool:
    """True when the reply signals the user doesn't know / can't find the answer."""
    return bool(_STUCK_RE.search(text or ""))


# How to obtain each piece of info we ask for (user-facing guidance).
HELP = {
    "machine_id": ("You can find the machine id on the asset label on the machine "
                   "(usually on the side or front), in the maintenance log, or by asking "
                   "your supervisor."),
    "symptom": ("Just describe what you're seeing — any message on the display (e.g. "
                "MINTEMP/MAXTEMP), an unusual sound, or a print defect such as not "
                "sticking, stringing, or layers shifting."),
    "comment": ("A short note on what was done or found is enough, e.g. \"replaced the "
                "thermistor\" or \"recalibrated the bed\"."),
    "incident_id": ("If you don't have the incident id, just say \"mine\", \"open\", "
                    "\"closed\", or \"all\" and I'll list the incidents so you can pick one."),
}

_CANCEL = ("If you can't get it right now, use the “✖ Cancel / ask something "
           "else” button to do something different.")


def guide(question: str, field: str) -> str:
    """A help-first reply for a stuck user: acknowledge + how-to-find-it + the ask + escape."""
    parts = ["No problem —", HELP.get(field, ""), question, _CANCEL]
    return " ".join(p for p in parts if p)


def give_up(field: str) -> str:
    """Final message when the re-ask cap is hit and the info is still missing."""
    help_text = HELP.get(field, "")
    tail = (" " + help_text) if help_text else ""
    return ("I'm sorry, I can't proceed without that information." + tail +
            " When you have it, just ask again and we'll pick up from there.")


# A clarification reply that means "stop this / I'm done", not an answer. Anchored to
# the WHOLE (trimmed) reply so a real answer that merely CONTAINS one of these words
# (e.g. a symptom "the printer won't stop heating") is not misread as a bail.
_BAIL_RE = re.compile(
    r"^\s*(cancel( it| that)?|never\s*mind|nevermind|nvm|forget\s*(it|that|about\s*it)"
    r"|stop( it| that)?|leave\s*it|drop\s*it|skip\s*it|no\s*thanks?|nothing( else)?"
    r"|none|ok(ay)?|k|done|that'?s\s*(all|it)|that'?ll\s*be\s*all)\s*[.!]*\s*$",
    re.I,
)


def is_bail(text: str) -> bool:
    """True when a clarification reply means 'stop / I'm done', not an answer."""
    return bool(_BAIL_RE.match((text or "").strip()))


def bailed() -> str:
    """Message shown when the user abandons a clarification (bail or topic pivot)."""
    return "Okay — I've stopped that. What would you like to do next?"
