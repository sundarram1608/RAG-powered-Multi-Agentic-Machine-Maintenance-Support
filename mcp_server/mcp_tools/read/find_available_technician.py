"""
find_available_technician — propose who should do the work and WHEN.

Read-only: it only proposes an assignment (with a concrete work date + slot);
book_technician_slot commits it. Search policy (earliest wins):
    1-3. a technician with an Available slot on the booking day, then +1, then +2
         (on the booking day, only slots starting after the booking time count);
    4.   else escalate to an active supervisor, given a 1-hour slot inside their
         9AM-5PM shift, on the booking day if one still fits, otherwise +1.

Used by: Technician Action (allocate someone to an incident); also allow-listed to Manage Incident.
"""

import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # .../mcp_tools
from _common import run_query, REFERENCE_TODAY

# A supervisor's bookable 1-hour slots: between 1h after shift start (9AM->10:00)
# and 1h before shift end (5PM->16:00). Starts 10..15 -> "10:00-11:00".."15:00-16:00".
SUPERVISOR_SLOT_START_HOURS = [10, 11, 12, 13, 14, 15]


def _slot_start(slot: str) -> time:
    """Start time of an 'HH:MM-HH:MM' slot string."""
    hh, mm = slot.split("-")[0].strip().split(":")
    return time(int(hh), int(mm))


def _resolve_moment(booking_moment):
    """(date, time) of the booking moment; default = REFERENCE_TODAY + now()."""
    if not booking_moment:
        return REFERENCE_TODAY, datetime.now().time()
    text = booking_moment.strip()
    if " " in text:
        d_part, t_part = text.split(" ", 1)
        parts = t_part.split(":")
        return (
            date.fromisoformat(d_part),
            time(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0),
        )
    return date.fromisoformat(text), time(0, 0)


def _supervisor_free_slot(supervisor_id, target_date, min_start):
    """Earliest free 1-hour supervisor slot on target_date (after min_start), or None."""
    taken = {
        r["availability_slot"]
        for r in run_query(
            "SELECT availability_slot FROM technician_schedule "
            "WHERE `date`=%s AND employee_id=%s",
            (str(target_date), supervisor_id),
        )
    }
    for hour in SUPERVISOR_SLOT_START_HOURS:
        start = time(hour, 0)
        if min_start is not None and start <= min_start:
            continue
        slot = f"{hour:02d}:00-{hour + 1:02d}:00"
        if slot not in taken:
            return slot
    return None


def find_available_technician(booking_moment: str | None = None) -> dict:
    """
    Propose who should do the work and on what date/slot, for an incident being
    booked at `booking_moment`. Searches for a technician on the booking day
    (slots after the booking time), then the next day, then the day after; if no
    technician is free across those three days, it escalates to a supervisor and
    proposes a 1-hour slot in their shift (same day if one still fits, else the
    next day). The returned `date` is the scheduled WORK date and may differ from
    the booking date. Read-only — pass the result to book_technician_slot to commit.

    Args:
        booking_moment: "YYYY-MM-DD HH:MM:SS" (or just a date). Defaults to the
                        system reference date (2026-06-16) + the current time.

    Returns one of:
        {assignee_role: "Technician", employee_id, date, availability_slot,
         shift_time, escalated: False}
        {assignee_role: "Supervisor", employee_id, date, availability_slot,
         escalated: True, note}                 # no technician in the 3-day window
        {available: False, note}                # no active supervisor either
    """
    booking_date, booking_time = _resolve_moment(booking_moment)

    # 1-3) Technician on day 0 (after booking time), then +1, then +2.
    for offset in (0, 1, 2):
        target = booking_date + timedelta(days=offset)
        rows = run_query(
            """
            SELECT employee_id, availability_slot, shift_time
            FROM technician_schedule
            WHERE `date`=%s AND availability_status='Available'
            ORDER BY availability_slot ASC, employee_id ASC
            """,
            (str(target),),
        )
        for row in rows:
            if offset == 0 and _slot_start(row["availability_slot"]) <= booking_time:
                continue  # slot already started/passed today
            return {
                "assignee_role": "Technician",
                "employee_id": row["employee_id"],
                "date": str(target),
                "availability_slot": row["availability_slot"],
                "shift_time": row["shift_time"],
                "escalated": False,
            }

    # 4) No technician in the 3-day window -> escalate to an active supervisor.
    supervisors = run_query(
        "SELECT employee_id FROM employees "
        "WHERE role='Supervisor' AND status='Active' LIMIT 1"
    )
    if supervisors:
        supervisor_id = supervisors[0]["employee_id"]
        for offset in (0, 1):
            target = booking_date + timedelta(days=offset)
            min_start = booking_time if offset == 0 else None
            slot = _supervisor_free_slot(supervisor_id, target, min_start)
            if slot:
                return {
                    "assignee_role": "Supervisor",
                    "employee_id": supervisor_id,
                    "date": str(target),
                    "availability_slot": slot,
                    "escalated": True,
                    "note": "No technician free within 3 days; escalated to a supervisor.",
                }

    return {"available": False, "note": "No technician or supervisor slot available."}


# === SELF-TEST — python mcp_server/mcp_tools/read/find_available_technician.py ===
if __name__ == "__main__":
    import json

    print("default (booking 2026-06-16 + now) -> earliest technician:")
    print(json.dumps(find_available_technician(), indent=2, default=str))

    print("\nlate on the booking day (rolls to a later day):")
    print(json.dumps(find_available_technician("2026-06-16 23:30:00"), indent=2, default=str))

    print("\nbeyond the seeded schedule (supervisor escalation, computed slot):")
    print(json.dumps(find_available_technician("2026-12-01 09:00:00"), indent=2, default=str))
