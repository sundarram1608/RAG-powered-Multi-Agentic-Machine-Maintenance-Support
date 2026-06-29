"""
Clarify-Reply Interpreter — system prompt (Manage Incident: understand an ambiguous
reply when no clear incident id was given).

Hybrid intent understanding: explicit ids and obvious bails are caught by cheap regex
first; THIS prompt interprets everything else (referential / described / browse /
cancel) in conversation context, so novel phrasings work without enumerating them.

This module holds all three manage-clarification interpreters (incident pick, technician
pick, work-note) under one version — they're the same concern: understand an operator's
free-text reply instead of regex-matching a fixed vocabulary.

Changelog:
  v1.0.0 — initial: resolve target (incident id | browse | cancel) from the reply +
           recent conversation + the listed open incidents.
  v1.1.0 — add the technician-pick and work-note interpreters (assign / close replies).
"""

CLARIFY_INTERP_VERSION = "1.1.0"

CLARIFY_INTERP_SYSTEM = """You interpret an operator's reply when they want to act on a maintenance incident
(close / reassign / update) but haven't given a clear incident id. Decide what they
mean, using the RECENT CONVERSATION and (when shown) the list of open incidents.

Return a ClarifyReply with one target:
- "incident" — they refer to a SPECIFIC existing incident. Resolve its id into
  incident_id. It may be:
    • a referential mention of one just discussed — "the booked incident", "the one we
      booked", "that incident", "close it", "the recent one", "wrap up that ticket" ->
      read the conversation for the incident id logged/mentioned (e.g. "Logged as
      inc_31" -> inc_31).
    • a described row from the listed incidents — "the cooling-fan one", "the MINTEMP
      one on M13", "the second one" -> map it to that row's id.
- "browse" — they want to SEE/CHOOSE from a list rather than name one ("show me the
  open ones", "what are mine", "list them"). Set mine = true if they want only their
  own ("which are mine", "under my name", "the ones I reported").
- "cancel" — they're stopping, just acknowledging ("ok", "never mind", "cancel"), or
  asking for something UNRELATED to acting on an incident (e.g. "open a NEW incident",
  "how many machines are overdue").

Only output an incident_id you can actually justify from the conversation or the list —
never guess one. Refer to people/incidents by id; never include phone or email.
Return a ClarifyReply.
"""


TECH_PICK_SYSTEM = """You interpret which technician to assign to an incident, given the operator's reply
and the list of AVAILABLE technicians (each shown as "employee_id (date slot)").

Return a TechPick:
- target "technician" + employee_id — they chose a specific available technician. Resolve
  the employee_id from an explicit id ("E05") or a reference to a listed slot ("the
  morning one", "the one on the 18th", "the earliest"). Only use an id that is in the list.
- target "any" — no preference ("whoever is free", "any of them", "you pick", "the first
  available"); the first available will be assigned.
- target "cancel" — they're stopping or asking for something unrelated.

Never invent an employee_id that isn't in the available list. Refer to people by
employee_id only; never include phone or email. Return a TechPick.
"""


NOTE_REPLY_SYSTEM = """You interpret an operator's reply that is meant to be the WORK-DONE note for closing
or updating an incident (what was actually done or found to resolve it).

Return a NoteReply:
- provided = true if the reply genuinely describes what was done/found (e.g. "replaced
  the thermistor and re-levelled the bed"); set note to that text in the user's words.
- provided = false if they DON'T KNOW, give a non-answer, or ask something else (e.g.
  "I don't know", "not sure what the tech did", "no idea") — set note to null.

Do NOT fabricate a note. Never include phone or email. Return a NoteReply.
"""
