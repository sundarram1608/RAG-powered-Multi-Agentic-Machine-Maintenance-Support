"""
send_email — notify an employee about an incident, from "Agentic FDM Services".

PII-safe: the recipient's address is resolved internally from their employee_id
and is NEVER passed in or returned. Content is chosen automatically from the
recipient's role (operator / technician / supervisor) and filled from the
incident. Sends via free Gmail SMTP (smtplib + an App Password in .env).
Used by: Action.
"""

import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # .../mcp_tools
from _common import run_query, ENV_PATH

SENDER_NAME = "Agentic FDM Services"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def _mask(email: str) -> str:
    """Mask an address for developer-facing logs (never in tool output)."""
    name, _, domain = (email or "").partition("@")
    return f"{name[0]}***@{domain}" if name and domain else "***"


def _schedule_text(incident: dict) -> str:
    work_date = incident["work_date"] or "(to be scheduled)"
    work_slot = incident["work_slot"] or ""
    return f"{work_date} and {work_slot}".strip()


def _compose(role: str, recipient_name: str, incident: dict, assignee: dict):
    """Return (subject, body) for the recipient's role."""
    inc_id = incident["incident_id"]
    machine = incident["machine_id"]
    complaint = incident["user_complaint"]
    root_cause = incident["agent_root_cause"]
    fix = incident["agentic_resolution"]
    schedule = _schedule_text(incident)

    if role == "Operator" and incident.get("incident_closure_date"):
        # The incident is closed -> a resolution notice, not a "logged" confirmation.
        work_done = (incident.get("technician_comments") or "").strip() or "—"
        subject = f"Your reported issue on {machine} has been resolved ({inc_id})"
        body = (
            f"Hi {recipient_name},\n"
            f"Good news — the incident you reported has been resolved and closed.\n"
            f"Incident : {inc_id}\n"
            f"Machine : {machine}\n"
            f"What you reported : {complaint}\n"
            f"Work done : {work_done}\n"
            f"Closed on : {incident['incident_closure_date']}\n\n"
            f"Thanks for reporting it.\n"
            f"— Agentic FDM Services"
        )
    elif role == "Operator":
        subject = f"Your reported issue on {machine} has been logged ({inc_id})"
        if assignee:
            allocated = f"{assignee['role']} Allocated: {assignee['full_name']}"
        else:
            allocated = "Technician Allocated: (to be assigned)"
        body = (
            f"Hi {recipient_name},\n"
            f"We've logged the issue you reported.\n"
            f"Incident : {inc_id}\n"
            f"Machine : {machine}\n"
            f"What you reported : {complaint}\n"
            f"AI Diagnosed Root cause: {root_cause}\n"
            f"AI Planned fix: {fix}\n"
            f"Scheduled: {schedule}\n"
            f"{allocated}\n\n"
            f"         We'll let you know once it's resolved.\n"
            f"— Agentic FDM Services"
        )
    elif role == "Technician":
        subject = f"New work assignment: {inc_id} on {machine} ({incident['work_date'] or 'TBD'})"
        body = (
            f"Hi {recipient_name},\n"
            f"You've been assigned to resolve an incident.\n"
            f"  Incident : {inc_id}\n"
            f"     Machine : {machine}\n"
            f"  Reported : {complaint}\n"
            f"  Root cause: {root_cause}\n"
            f"  Recommended fix: {fix}\n"
            f"  Scheduled: {schedule}\n\n"
            f"Please confirm once completed.\n"
            f"— Agentic FDM Services"
        )
    else:  # Supervisor
        subject = f"Escalation: {inc_id} on {machine} requires your attention"
        body = (
            f"Hi {recipient_name},\n"
            f"This incident was escalated to you (no technician slot was free within the window).\n"
            f"  Incident : {inc_id}\n"
            f"     Machine : {machine}\n"
            f"  Reported : {complaint}\n"
            f"  Root cause: {root_cause}\n"
            f"  Recommended fix: {fix}\n"
            f"  Scheduled: {schedule}\n\n"
            f"Please handle or reassign as appropriate.\n"
            f"— Agentic FDM Services"
        )
    return subject, body


def send_email(to_employee_id: str, incident_id: str, dry_run: bool = False) -> dict:
    """
    Notify an employee about an incident by email, from "Agentic FDM Services". The
    recipient's address is resolved internally from their employee_id — it is
    NEVER passed in or returned (PII). The message content is chosen automatically
    from the recipient's role (operator = report confirmation, or a resolution notice
    if the incident is already closed; technician = work assignment; supervisor =
    escalation) and filled from the incident record.

    Args:
        to_employee_id: Who to notify, e.g. "E13". Their email is looked up internally.
        incident_id: The incident the email is about, e.g. "inc_26".
        dry_run: If True, compose but DO NOT send (returns subject/body for review).
                 Defaults to False — the email is actually sent via Gmail.

    Returns:
        {ok: True, dry_run: False, to_employee_id, role, subject, sent: True}   # sent
        {ok: True, dry_run: True,  to_employee_id, role, subject, body}          # composed only
        {ok: False, error}   # unknown employee/incident, no email on file, or email not configured
    """
    to_employee_id = (to_employee_id or "").strip().upper()
    incident_id = (incident_id or "").strip()

    emp = run_query(
        "SELECT full_name, email, role FROM employees WHERE employee_id=%s",
        (to_employee_id,),
    )
    if not emp:
        return {"ok": False, "error": f"Unknown employee '{to_employee_id}'."}
    recipient = emp[0]
    if not recipient["email"]:
        return {"ok": False, "error": f"No email on file for '{to_employee_id}'."}

    inc = run_query(
        """
        SELECT incident_id, machine_id, user_complaint, agent_root_cause,
               agentic_resolution, technician_id, work_date, work_slot,
               incident_closure_date, technician_comments
        FROM incidents WHERE incident_id=%s
        """,
        (incident_id,),
    )
    if not inc:
        return {"ok": False, "error": f"Unknown incident '{incident_id}'."}
    incident = inc[0]
    incident["work_date"] = str(incident["work_date"]) if incident["work_date"] else None
    incident["incident_closure_date"] = (
        str(incident["incident_closure_date"]) if incident["incident_closure_date"] else None)

    # Resolve the allocated person's name (for the operator template).
    assignee = None
    if incident["technician_id"]:
        rows = run_query(
            "SELECT full_name, role FROM employees WHERE employee_id=%s",
            (incident["technician_id"],),
        )
        if rows:
            assignee = rows[0]

    subject, body = _compose(recipient["role"], recipient["full_name"], incident, assignee)

    if dry_run:
        return {"ok": True, "dry_run": True, "to_employee_id": to_employee_id,
                "role": recipient["role"], "subject": subject, "body": body}

    load_dotenv(dotenv_path=ENV_PATH, override=True)
    agent_email = os.getenv("AGENT_EMAIL")
    app_password = os.getenv("AGENT_EMAIL_APP_PASSWORD")
    if not agent_email or not app_password:
        return {"ok": False,
                "error": "email not configured (set AGENT_EMAIL and "
                         "AGENT_EMAIL_APP_PASSWORD in .env)."}

    msg = EmailMessage()
    msg["From"] = f"{SENDER_NAME} <{agent_email}>"
    msg["To"] = recipient["email"]
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls(context=context)
            server.login(agent_email, app_password)
            server.send_message(msg)
    except Exception as exc:
        return {"ok": False, "error": f"send failed: {exc}"}

    return {"ok": True, "dry_run": False, "to_employee_id": to_employee_id,
            "role": recipient["role"], "subject": subject, "sent": True}


# === SELF-TEST — python mcp_server/mcp_tools/other/send_email.py ===
if __name__ == "__main__":
    import json

    # An incident that has an assignee + schedule (for a full operator template).
    inc = run_query(
        "SELECT incident_id, reported_by, technician_id FROM incidents "
        "WHERE technician_id IS NOT NULL AND work_date IS NOT NULL "
        "ORDER BY incident_id LIMIT 1"
    )[0]
    inc_id = inc["incident_id"]
    operator_id = inc["reported_by"]
    technician_id = inc["technician_id"]
    supervisor_id = run_query(
        "SELECT employee_id FROM employees WHERE role='Supervisor' AND status='Active' LIMIT 1"
    )[0]["employee_id"]

    print(f"Using incident {inc_id}: operator={operator_id}, technician={technician_id}, "
          f"supervisor={supervisor_id}\n")

    for label, emp_id in [("OPERATOR", operator_id), ("TECHNICIAN", technician_id),
                          ("SUPERVISOR", supervisor_id)]:
        res = send_email(emp_id, inc_id, dry_run=True)
        # PII guard: the returned dict must NOT contain an email address.
        assert "email" not in res and "@" not in str(res.get("to_employee_id", "")), res
        masked = _mask(run_query("SELECT email FROM employees WHERE employee_id=%s",
                                 (emp_id,))[0]["email"])
        print(f"===== {label}  (would send to {masked}) =====")
        print("subject:", res["subject"])
        print(res["body"])
        print()

    print("unknown employee ->", send_email("E99", inc_id, dry_run=True))
    print("unknown incident ->", send_email(operator_id, "inc_nope", dry_run=True))

    # --- Live send (only if AGENT_EMAIL is configured) ---
    load_dotenv(dotenv_path=ENV_PATH, override=True)
    if os.getenv("AGENT_EMAIL") and os.getenv("AGENT_EMAIL_APP_PASSWORD"):
        print("\nAGENT_EMAIL configured -> sending one real email to the operator…")
        print("live ->", json.dumps(send_email(operator_id, inc_id), default=str))
    else:
        print("\n(Live send skipped — set AGENT_EMAIL + AGENT_EMAIL_APP_PASSWORD in .env "
              "to test an actual Gmail send.)")
