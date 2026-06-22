"""
get_overdue_status — is a machine overdue for preventive maintenance?

Overdue = latest preventive service_date + the version's service_interval_days
is before REFERENCE_TODAY. Used by: Diagnosis (a strong root-cause signal).
"""

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # .../mcp_tools
from _common import run_query, REFERENCE_TODAY


def get_overdue_status(machine_id: str) -> dict:
    """
    Determine whether a machine is overdue for preventive maintenance. A machine
    that is overdue is a STRONG root-cause signal — check this early in diagnosis,
    since many faults trace back to a missed service.

    Overdue = last service date + the version's service interval is earlier than
    the system reference date (2026-06-16, the dataset's "today" — not the real
    clock).

    Args:
        machine_id: Machine tag like "M01" (case-insensitive).

    Returns one of:
        {exists: False, machine_id}                          # unknown machine
        {machine_id, has_history: False, overdue: None}      # never serviced
        {machine_id, has_history: True, last_service_date, interval_days,
         next_due_date, overdue: bool, days_overdue}         # days_overdue=0 if not overdue
    """
    machine_id = (machine_id or "").strip().upper()

    interval_rows = run_query(
        """
        SELECT v.service_interval_days
        FROM machines m JOIN machine_versions v ON m.mvc_code = v.mvc_code
        WHERE m.machine_id = %s
        """,
        (machine_id,),
    )
    if not interval_rows:
        return {"exists": False, "machine_id": machine_id}
    interval = interval_rows[0]["service_interval_days"]

    last_rows = run_query(
        "SELECT MAX(service_date) AS last_service "
        "FROM maintenance_history WHERE machine_id = %s",
        (machine_id,),
    )
    last_service = last_rows[0]["last_service"] if last_rows else None
    if last_service is None:
        return {"machine_id": machine_id, "has_history": False, "overdue": None}

    next_due = last_service + timedelta(days=interval)
    overdue = REFERENCE_TODAY > next_due
    return {
        "machine_id": machine_id,
        "has_history": True,
        "last_service_date": str(last_service),
        "interval_days": interval,
        "next_due_date": str(next_due),
        "overdue": overdue,
        "days_overdue": (REFERENCE_TODAY - next_due).days if overdue else 0,
    }


# === SELF-TEST — python mcp_server/mcp_tools/read/get_overdue_status.py ===
if __name__ == "__main__":
    print("M03 (seeded overdue) ->", get_overdue_status("M03"))
    print("M01                  ->", get_overdue_status("M01"))
    print("M99 (missing)        ->", get_overdue_status("M99"))
