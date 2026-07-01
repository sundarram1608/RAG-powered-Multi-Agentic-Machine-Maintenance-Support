"""
manage_incident.py — Manage Incident Agent: direct actions on a KNOWN incident.

Two phases (one agent), with an approval/clarification interrupt between:
  manage_resolve : resolve the incident (get_incident) + LLM-plan the action
                   (close / assign / update_comment / unsupported). For "assign"
                   it resolves a technician from LIVE availability
                   (list_available_technicians) — named-&-available → propose;
                   else present the list and ask the manager to choose.
  manage_execute : mechanical — perform the approved action via the write/booking
                   tools and notify (send_email).

The availability rules live HERE (the node), not the prompt: the LLM only extracts
intent (action + named technician); the node enforces who is actually free.

LLM (resolve): Groq Llama 3.3 70B. execute: no LLM.
Tools: get_incident, list_available_technicians, book_technician_slot,
       update_incident, send_email.
Input  (reads state): user_input (+ carried manage_plan on resume), current_user_id.
Output (writes state): manage_plan (+ enrichment), needs_clarification /
       clarification_question / requires_approval; execute -> action_result;
       prompt_versions["manage_incident"].
Structured output: Pydantic `ManagePlan` via with_structured_output.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agents/ on path
from utils import clarify
import config
from utils import history
import mcp_client
from utils import streaming
from llms import get_reasoner
from schemas import ClarifyReply, ManagePlan, NoteReply, TechPick
from prompts.clarify_interp import CLARIFY_INTERP_SYSTEM, NOTE_REPLY_SYSTEM, TECH_PICK_SYSTEM
from prompts.manage_incident import MANAGE_RESOLVE_SYSTEM, MANAGE_RESOLVE_VERSION

from langchain_core.messages import HumanMessage, SystemMessage

_INC_RE = re.compile(r"inc_\d+", re.IGNORECASE)
_EMP_RE = re.compile(r"E\d+", re.IGNORECASE)


async def _interpret_reply(user_input: str, state: dict, open_incidents: list | None) -> ClarifyReply:
    """Decide what an operator's reply means when there's no clear incident id: a
    specific incident (resolve its id from context/list), browse, or cancel. HYBRID —
    cheap regex first (explicit id, obvious bail); the LLM (in conversation context)
    handles everything else, so novel phrasings work without enumerating them."""
    m = _INC_RE.search(user_input or "")
    if m:                                   # explicit id -> no LLM needed
        return ClarifyReply(target="incident", incident_id=m.group(0).lower())
    if clarify.is_bail(user_input):         # obvious "ok"/"cancel"/"never mind"
        return ClarifyReply(target="cancel")
    context = history.format_recent(state.get("messages") or [], max_exchanges=5)
    listing = "\n".join(
        f"- {it['incident_id']}: {it.get('machine_id')} — {(it.get('summary') or '').strip()}"
        for it in (open_incidents or []))
    human = (f"Recent conversation:\n{context or '(none)'}\n\n"
             f"Open incidents shown:\n{listing or '(none shown yet)'}\n\n"
             f"Operator reply: {user_input}")
    return get_reasoner(structured=ClarifyReply).invoke(
        [SystemMessage(content=CLARIFY_INTERP_SYSTEM), HumanMessage(content=human)])


async def _interpret_tech(user_input: str, available: list) -> TechPick:
    """Which technician to assign — hybrid: regex for an explicit E## id, else the LLM
    resolves a slot/date reference or 'whoever's free' against the available list."""
    m = _EMP_RE.search(user_input or "")
    if m:
        return TechPick(target="technician", employee_id=m.group(0).upper())
    if clarify.is_bail(user_input):
        return TechPick(target="cancel")
    human = f"Available technicians: {_fmt(available)}\n\nOperator reply: {user_input}"
    return get_reasoner(structured=TechPick).invoke(
        [SystemMessage(content=TECH_PICK_SYSTEM), HumanMessage(content=human)])


async def _interpret_note(user_input: str) -> NoteReply:
    """Is the reply a real work-done note, or a 'don't know'? LLM-judged (the caller
    handles an obvious bail via is_bail before calling)."""
    return get_reasoner(structured=NoteReply).invoke(
        [SystemMessage(content=NOTE_REPLY_SYSTEM),
         HumanMessage(content=f"Operator reply: {user_input}")])


async def _call(name: str, args: dict, expect_list: bool = False):
    streaming.emit_tool(name, args)
    tools = await mcp_client.get_all_tools()
    tool = next(t for t in tools if t.name == name)
    return mcp_client.parse_tool_result(await tool.ainvoke(args), expect_list=expect_list)


async def _list_techs(employee_id=None):
    args = {"employee_id": employee_id} if employee_id else {}
    return await _call("list_available_technicians", args, expect_list=True)


def _fmt(techs: list) -> str:
    return "; ".join(f"{t['employee_id']} ({t['date']} {t['availability_slot']})"
                     for t in techs) or "(none available)"


def _clarify(plan: dict, question: str, versions: dict) -> dict:
    plan = {**plan, "needs_clarification": True, "question": question}
    return {"manage_plan": plan, "needs_clarification": True,
            "clarification_question": question, "prompt_versions": versions}


def _md_cell(value) -> str:
    """Sanitize a value for a Markdown table cell (no pipes / newlines breaking it)."""
    s = str(value if value is not None else "—").replace("\n", " ").replace("|", "\\|").strip()
    return s or "—"


def _format_incident_list(incidents: list, status: str, mine: bool) -> str:
    scope = " you reported or are assigned to" if mine else ""
    if not incidents:
        return (f"I couldn't find any {status} incidents{scope}. You can give an id "
                "(e.g. inc_26), say 'all' / 'closed' to widen the search, or describe a "
                "new fault to open one.")
    # Render as a Markdown table (the app shows clarify questions via st.markdown).
    show_closed = any(it.get("status") == "closed" for it in incidents)
    headers = ["Incident", "Machine", "Reported by", "Assigned to", "Reported", "Complaint"]
    if show_closed:                            # closed rows also carry the resolution
        headers += ["Root cause", "Suggested", "Technician did"]
    body = []
    for it in incidents:
        cells = [it.get("incident_id"), it.get("machine_id"),
                 it.get("reported_by"), it.get("technician_id"),
                 it.get("reported_date"), it.get("summary")]
        if show_closed:
            cells += [it.get("agent_root_cause"), it.get("agent_suggested_action"),
                      it.get("technician_action")]
        body.append("| " + " | ".join(_md_cell(c) for c in cells) + " |")
    table = ("| " + " | ".join(headers) + " |\n"
             "| " + " | ".join("---" for _ in headers) + " |\n"
             + "\n".join(body))
    tip = "Reply with an id (e.g. inc_22)" + ("." if mine else ", or say 'mine' to see only yours.")
    return (f"Which incident would you like to act on? Here are the open "
            f"incidents{scope}:\n\n" + table + "\n\n" + tip)


async def _open_incidents(state: dict, mine: bool = False) -> list:
    """Open incidents (optionally only the current operator's) — for the picker and for
    giving the reply-interpreter the rows it can resolve a described pick against."""
    employee_id = state.get("current_user_id") if mine else None
    return await _call("list_incidents", {"status": "open", "employee_id": employee_id},
                       expect_list=True)


async def _browse_clarify(state: dict, versions: dict, original_request: str,
                          mine: bool = False, incidents: list | None = None) -> dict:
    """List incidents for the user to pick one to ACT ON, and carry the ORIGINAL request
    so the chosen id resumes with the right intent. Always scoped to OPEN incidents:
    every manage action (close/assign/update) applies to an open incident — you can't
    act on a closed one. `mine` filters to the current operator. Pass `incidents` to
    reuse an already-fetched list."""
    if incidents is None:
        incidents = await _open_incidents(state, mine)
    plan = {"action": None, "browsing": True, "original_request": original_request}
    return _clarify(plan, _format_incident_list(incidents, "open", mine), versions)


async def manage_resolve(state: dict) -> dict:
    user_input = state.get("user_input", "")
    versions = dict(state.get("prompt_versions", {}))
    versions["manage_incident"] = MANAGE_RESOLVE_VERSION
    prior = state.get("manage_plan") or {}

    def _cancelled(base: dict) -> dict:
        return {"manage_plan": {**base, "action": "cancelled"},
                "action_result": {"action": "cancelled"}, "clarify_abandoned": True,
                "final_response": clarify.bailed(), "prompt_versions": versions}

    def _assigned(named, t):
        plan = {**prior, "named_employee": named, "assign_target": t, "needs_clarification": False,
                "plan_summary": f"Assign {named} ({t['date']} {t['availability_slot']}) "
                                f"to {prior['incident_id']}.", "requires_approval": True}
        return {"manage_plan": plan, "requires_approval": True, "prompt_versions": versions}

    # --- resume: we previously asked the manager to choose a technician ---
    if prior.get("action") == "assign" and prior.get("needs_clarification"):
        available = await _list_techs()
        pick = await _interpret_tech(user_input, available)
        if pick.target == "cancel":
            return _cancelled(prior)
        if pick.target == "technician" and pick.employee_id:
            named = pick.employee_id
            avail = await _list_techs(named)
            if avail:
                return _assigned(named, avail[0])
            return _clarify({**prior, "named_employee": named},
                            f"{named} has no free slot. Available: {_fmt(available)}. "
                            f"Which should I assign?", versions)
        # "any" / no preference -> the first available technician
        if available:
            return _assigned(available[0]["employee_id"], available[0])
        pd = {**prior, "action": "unsupported",
              "plan_summary": "No technicians are currently available to assign."}
        return {"manage_plan": pd, "prompt_versions": versions}

    # --- resume: we previously asked for a closing / update note ---
    if prior.get("needs_clarification") and prior.get("action") in ("close", "update_comment"):
        if clarify.is_bail(user_input):            # "never mind" mid-note -> stop the action
            return _cancelled(prior)
        nr = await _interpret_note(user_input)     # real note vs "I don't know" (LLM-judged)
        if not nr.provided:
            q = prior.get("question") or "What was done or found?"
            return _clarify(prior, clarify.guide(q, "comment"), versions)
        comment = (nr.note or user_input).strip()
        verb = "Close" if prior["action"] == "close" else "Update"
        pd = {**prior, "comment": comment, "needs_clarification": False, "question": None,
              "requires_approval": True,
              "plan_summary": f"{verb} incident {prior['incident_id']} with the note: \"{comment}\"."}
        return {"manage_plan": pd, "requires_approval": True, "prompt_versions": versions}

    # --- resume: we previously listed incidents and asked the user to pick one ---
    if prior.get("browsing") and prior.get("needs_clarification"):
        original = prior.get("original_request") or user_input
        reply = await _interpret_reply(user_input, state, await _open_incidents(state))
        if reply.target == "cancel":
            return _cancelled(prior)                       # bail / pivot -> stop, don't re-list
        if reply.target == "browse":
            return await _browse_clarify(state, versions, original, mine=reply.mine)
        if reply.target == "incident" and reply.incident_id:
            incident_id = reply.incident_id
            user_input = original                          # re-infer the action from the original ask
        else:
            return await _browse_clarify(state, versions, original)   # couldn't pin one -> re-list
    else:
        # --- resolve the incident id (carried, explicit, or interpreted in context) ---
        incident_id = prior.get("incident_id")
        if not incident_id:
            m = _INC_RE.search(user_input)
            incident_id = m.group(0).lower() if m else None
        if not incident_id:
            # "open / create / log / book a NEW incident" -> troubleshoot, not manage.
            if re.search(r"\b(new|open|create|raise|log|file|start|book)\b[\w\s,]*\bincident\b",
                         user_input, re.I):
                plan = {"action": "unsupported", "incident_id": None,
                        "plan_summary": "I can only act on an existing incident here. To open a new "
                                        "one, just describe the fault (e.g. \"M03's bed won't heat\") and "
                                        "I'll diagnose it and log the incident for you. To edit an existing "
                                        "incident, give its id (e.g. inc_26)."}
                return {"manage_plan": plan, "prompt_versions": versions}
            # interpret in context: a referential/described mention -> resolve its id;
            # a "show me" -> browse; a bail/pivot -> stop.
            reply = await _interpret_reply(user_input, state, None)
            if reply.target == "cancel":
                return _cancelled({})
            if reply.target == "incident" and reply.incident_id:
                incident_id = reply.incident_id
            else:
                return await _browse_clarify(state, versions, user_input, mine=reply.mine)

    incident = await _call("get_incident", {"incident_id": incident_id})
    if not incident.get("exists"):
        return _clarify({"action": None, "incident_id": incident_id},
                        f"I couldn't find incident {incident_id}. Please confirm the id.", versions)

    # --- LLM plans the action from the request + live incident details ---
    details = (f"incident_id={incident['incident_id']}, machine={incident['machine_id']}, "
               f"status={incident['status']}, current_assignee={incident.get('technician_id')}, "
               f"work_date={incident.get('work_date')}")
    plan = get_reasoner(structured=ManagePlan).invoke([
        SystemMessage(content=MANAGE_RESOLVE_SYSTEM),
        HumanMessage(content=f"User request: {user_input}\n\nIncident details: {details}"),
    ])
    pd = plan.model_dump()
    pd["incident_id"] = incident_id
    pd["reported_by"] = incident.get("reported_by")   # for operator notification

    if pd.get("needs_clarification"):                 # e.g. close without a comment
        return _clarify(pd, pd.get("question") or "Could you clarify the request?", versions)

    if pd["action"] == "assign":
        named = pd.get("named_employee")
        if named:
            avail = await _list_techs(named)
            if avail:
                t = avail[0]
                pd.update(assign_target=t, requires_approval=True,
                          plan_summary=f"Assign {named} ({t['date']} {t['availability_slot']}) "
                                       f"to {incident_id} on {incident['machine_id']}.")
                return {"manage_plan": pd, "requires_approval": True, "prompt_versions": versions}
            all_av = await _list_techs()
            return _clarify(pd, f"{named} has no free slot. Available: {_fmt(all_av)}. "
                                f"Which should I assign?", versions)
        all_av = await _list_techs()
        if not all_av:
            pd.update(action="unsupported", plan_summary="No technicians are currently available to assign.")
            return {"manage_plan": pd, "prompt_versions": versions}
        return _clarify(pd, f"Which technician should I assign to {incident_id} on "
                            f"{incident['machine_id']}? Reply with an id (e.g. E05). "
                            f"Available: {_fmt(all_av)}.", versions)

    pd["requires_approval"] = pd["action"] in ("close", "update_comment")
    return {"manage_plan": pd, "requires_approval": pd["requires_approval"],
            "prompt_versions": versions}


async def manage_execute(state: dict) -> dict:
    """Mechanical: perform the approved action and notify (no LLM)."""
    plan = state["manage_plan"]
    action, inc = plan["action"], plan["incident_id"]
    dry = state.get("email_dry_run", False)
    emails = []

    if action == "close":
        result = await _call("update_incident",
                             {"incident_id": inc, "technician_comments": plan["comment"], "close": True})
        if plan.get("reported_by"):
            await _call("send_email", {"to_employee_id": plan["reported_by"], "incident_id": inc, "dry_run": dry})
            emails.append(plan["reported_by"])
        return {"action_result": {"action": "close", "result": result, "emails_sent": emails}}

    if action == "update_comment":
        result = await _call("update_incident",
                             {"incident_id": inc, "technician_comments": plan["comment"], "close": False})
        return {"action_result": {"action": "update_comment", "result": result}}

    if action == "assign":
        t = plan["assign_target"]
        result = await _call("book_technician_slot",
                             {"incident_id": inc, "employee_id": t["employee_id"],
                              "date": t["date"], "availability_slot": t["availability_slot"]})
        await _call("send_email", {"to_employee_id": t["employee_id"], "incident_id": inc, "dry_run": dry})
        emails.append(t["employee_id"])
        if plan.get("reported_by"):
            await _call("send_email", {"to_employee_id": plan["reported_by"], "incident_id": inc, "dry_run": dry})
            emails.append(plan["reported_by"])
        return {"action_result": {"action": "assign", "result": result, "emails_sent": emails}}

    return {"action_result": {"action": action, "note": "no action taken (unsupported)"}}


# === SELF-TEST — full resolve(+execute) paths. Needs GROQ key AND the HTTP server:
#     python mcp_server/server.py http        # (separate terminal)
#     python agents/nodes/manage_incident.py
# Uses email_dry_run=True (no real emails) and create-then-clean for writes.
# ============================================================================
if __name__ == "__main__":
    import asyncio
    sys.path.insert(0, str(config.PROJECT_ROOT / "mcp_server" / "mcp_tools"))
    sys.path.insert(0, str(config.PROJECT_ROOT / "mcp_server" / "mcp_tools" / "write"))
    from _common import get_connection
    from create_incident import create_incident

    def _del(inc_id):
        c = get_connection(); cur = c.cursor()
        cur.execute("DELETE FROM incidents WHERE incident_id=%s", (inc_id,))
        c.commit(); c.close()

    def _free_slot(date, emp):
        c = get_connection(); cur = c.cursor()
        cur.execute("UPDATE technician_schedule SET availability_status='Available' "
                    "WHERE `date`=%s AND employee_id=%s", (date, emp))
        c.commit(); c.close()

    def _sweep():  # belt-and-suspenders: remove any leftover selftest incidents
        c = get_connection(); cur = c.cursor()
        cur.execute("DELETE FROM incidents WHERE user_complaint LIKE '[SELFTEST]%'")
        c.commit(); c.close()

    async def _resolve_print(label, state):
        out = await manage_resolve(state)
        p = out["manage_plan"]
        print(f"\n[{label}] action={p.get('action')} needs_clar={out.get('needs_clarification', False)} "
              f"approval={out.get('requires_approval', False)}")
        print(f"   {out.get('clarification_question') or p.get('plan_summary')}")
        return out

    async def _main():
        # --- resolve-only paths (reads) ---
        await _resolve_print("no id", {"user_input": "please close the incident"})
        await _resolve_print("unknown id", {"user_input": "close inc_999"})

        # --- close path (create -> resolve -> execute -> verify -> clean) ---
        inc = create_incident("M01", "E01", "[SELFTEST] manage close", "rc", "res")["incident_id"]
        await _resolve_print("close no comment", {"user_input": f"mark {inc} complete"})
        out = await _resolve_print("close w/ comment",
                                   {"user_input": f"close {inc}; replaced the thermistor and verified"})
        if out["manage_plan"]["action"] == "close":
            res = await manage_execute({**out, **out, "email_dry_run": True})
            print("   execute ->", res["action_result"]["result"].get("status"),
                  "| emails:", res["action_result"]["emails_sent"])
        _del(inc)

        # --- assign path (create -> resolve lists -> pick -> execute -> verify -> clean) ---
        inc2 = create_incident("M01", "E01", "[SELFTEST] manage assign", "rc", "res")["incident_id"]
        out = await _resolve_print("assign (generic)", {"user_input": f"assign a technician to {inc2}"})
        # simulate the manager picking the first listed technician
        import re as _re
        listed = _re.search(r"E\d+", out["clarification_question"])
        if listed:
            pick = listed.group(0)
            out2 = await _resolve_print(f"assign pick {pick}",
                                        {"user_input": pick, "manage_plan": out["manage_plan"]})
            if out2.get("requires_approval"):
                res = await manage_execute({**out2, "email_dry_run": True})
                print("   execute ->", res["action_result"]["result"].get("ok"),
                      "| reassigned_from:", res["action_result"]["result"].get("reassigned_from"),
                      "| emails:", res["action_result"]["emails_sent"])
                tgt = out2["manage_plan"]["assign_target"]      # free the slot we booked
                _free_slot(tgt["date"], tgt["employee_id"])
        _del(inc2)
        _sweep()
        print("\ncleanup done (incidents removed, booked slot freed)")

    asyncio.run(_main())
