"""
book_technician_slot — assign someone to an incident and book their slot.

Scoped write (one transaction via the least-privilege write user):
  * technician (schedule row exists & Available) -> UPDATE it to 'Booked';
  * supervisor escalation (no schedule row)      -> INSERT a 'Booked' row;
then UPDATE the incident's technician_id + work_date + work_slot.
Used by: Action (after find_available_technician).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # .../mcp_tools
from _common import run_query, run_write


def book_technician_slot(incident_id: str, employee_id: str, date: str,
                         availability_slot: str) -> dict:
    """
    Assign someone to an OPEN incident and book their slot, using the output of
    find_available_technician (which always returns a date + availability_slot).
    If a schedule row exists for that (date, employee_id) it is marked Booked
    (technician); if not, a new Booked row is inserted (supervisor escalation).
    The incident's technician_id, work_date, and work_slot are set in the same
    transaction.

    Args:
        incident_id: The open incident to staff, e.g. "inc_26".
        employee_id: The technician or supervisor to assign, e.g. "E13".
        date: The scheduled work date "YYYY-MM-DD".
        availability_slot: The slot window, e.g. "09:00-11:00".

    Returns:
        {ok: True, incident_id, employee_id, assignment_type, booked_slot:{date, availability_slot}}
        {ok: False, error}   # unknown/closed incident, unknown employee, slot already booked
    """
    incident_id = (incident_id or "").strip()
    employee_id = (employee_id or "").strip().upper()
    date = (date or "").strip()
    availability_slot = (availability_slot or "").strip()

    incident = run_query(
        "SELECT incident_closure_date FROM incidents WHERE incident_id=%s",
        (incident_id,),
    )
    if not incident:
        return {"ok": False, "error": f"Unknown incident '{incident_id}'."}
    if incident[0]["incident_closure_date"] is not None:
        return {"ok": False, "error": f"Incident '{incident_id}' is already closed."}

    if not run_query("SELECT 1 FROM employees WHERE employee_id=%s", (employee_id,)):
        return {"ok": False, "error": f"Unknown employee '{employee_id}'."}

    existing = run_query(
        "SELECT availability_status FROM technician_schedule "
        "WHERE `date`=%s AND employee_id=%s",
        (date, employee_id),
    )

    statements = []
    if existing:
        if existing[0]["availability_status"] == "Booked":
            return {"ok": False,
                    "error": f"{employee_id}'s slot on {date} is already booked."}
        statements.append((
            "UPDATE technician_schedule SET availability_status='Booked' "
            "WHERE `date`=%s AND employee_id=%s",
            (date, employee_id),
        ))
        assignment_type = "technician"
    else:
        statements.append((
            "INSERT INTO technician_schedule "
            "(`date`, employee_id, shift_time, availability_slot, availability_status) "
            "VALUES (%s, %s, NULL, %s, 'Booked')",
            (date, employee_id, availability_slot),
        ))
        assignment_type = "supervisor"

    statements.append((
        "UPDATE incidents SET technician_id=%s, work_date=%s, work_slot=%s "
        "WHERE incident_id=%s",
        (employee_id, date, availability_slot, incident_id),
    ))
    run_write(statements)

    return {
        "ok": True,
        "incident_id": incident_id,
        "employee_id": employee_id,
        "assignment_type": assignment_type,
        "booked_slot": {"date": date, "availability_slot": availability_slot},
    }


# === SELF-TEST — python mcp_server/mcp_tools/write/book_technician_slot.py ===
if __name__ == "__main__":
    import json
    from _common import get_connection
    from create_incident import create_incident

    def _cleanup(incident_id, revert_slot=None, delete_slot=None):
        conn = get_connection(); cur = conn.cursor()
        if revert_slot:
            cur.execute("UPDATE technician_schedule SET availability_status='Available' "
                        "WHERE `date`=%s AND employee_id=%s", revert_slot)
        if delete_slot:
            cur.execute("DELETE FROM technician_schedule WHERE `date`=%s AND employee_id=%s",
                        delete_slot)
        cur.execute("DELETE FROM incidents WHERE incident_id=%s", (incident_id,))
        conn.commit(); cur.close(); conn.close()

    # --- technician path: book an existing Available slot ---
    inc = create_incident("M01", "E01", "[SELFTEST] booking", "rc", "res")["incident_id"]
    slot = run_query("SELECT employee_id, `date`, availability_slot FROM technician_schedule "
                     "WHERE availability_status='Available' LIMIT 1")[0]
    res = book_technician_slot(inc, slot["employee_id"], str(slot["date"]),
                               slot["availability_slot"])
    print("technician ->", json.dumps(res, default=str))
    chk = run_query("SELECT availability_status FROM technician_schedule "
                    "WHERE `date`=%s AND employee_id=%s",
                    (str(slot["date"]), slot["employee_id"]))[0]
    inc_row = run_query("SELECT technician_id, work_date, work_slot FROM incidents "
                        "WHERE incident_id=%s", (inc,))[0]
    print("  slot now:", chk["availability_status"], "| incident:", inc_row)
    _cleanup(inc, revert_slot=(str(slot["date"]), slot["employee_id"]))

    # --- supervisor path: no schedule row -> INSERT one ---
    inc2 = create_incident("M01", "E01", "[SELFTEST] escalation", "rc", "res")["incident_id"]
    res2 = book_technician_slot(inc2, "E04", "2026-12-01", "10:00-11:00")
    print("supervisor ->", json.dumps(res2, default=str))
    inserted = run_query("SELECT availability_status, shift_time FROM technician_schedule "
                         "WHERE `date`='2026-12-01' AND employee_id='E04'")
    print("  inserted row:", inserted)
    _cleanup(inc2, delete_slot=("2026-12-01", "E04"))

    # --- closed-incident rejection ---
    print("closed     ->", book_technician_slot("inc_1", "E04", "2026-12-02", "10:00-11:00"))
