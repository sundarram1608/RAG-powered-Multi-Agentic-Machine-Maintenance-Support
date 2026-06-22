"""
list_available_technicians — available technician slots to CHOOSE from.

Used by: Manage Incident (let a manager pick a technician to assign). Unlike
find_available_technician (which auto-picks the single earliest slot and escalates
to a supervisor), this RETURNS A LIST — one earliest free slot per active
technician — so the caller can present options and let the user choose.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # .../mcp_tools
from _common import run_query, REFERENCE_TODAY


def list_available_technicians(from_date: str | None = None,
                               employee_id: str | None = None) -> list:
    """
    List active technicians who have a free slot on/after a date — earliest slot
    per technician.

    Use this to present assignable technicians for a manager to choose from. Pass
    `employee_id` to check ONE technician's availability (an empty list means that
    technician has no free slot).

    Args:
        from_date: Earliest date to consider, "YYYY-MM-DD" (default: the system
                   reference date, 2026-06-16).
        employee_id: Optional — restrict to a single technician (case-insensitive).

    Returns (earliest free slot per technician, soonest first):
        [{employee_id, date, availability_slot, shift_time}, ...]
        []   # nobody (matching) is available
    """
    from_date = from_date or str(REFERENCE_TODAY)
    sql = (
        "SELECT ts.employee_id, ts.`date`, ts.availability_slot, ts.shift_time "
        "FROM technician_schedule ts "
        "JOIN employees e ON ts.employee_id = e.employee_id "
        "WHERE ts.availability_status = 'Available' AND ts.`date` >= %s "
        "AND e.role = 'Technician' AND e.status = 'Active' "
    )
    params = [from_date]
    if employee_id:
        sql += "AND ts.employee_id = %s "
        params.append(employee_id.strip().upper())
    sql += "ORDER BY ts.`date` ASC, ts.availability_slot ASC"

    rows = run_query(sql, tuple(params))

    # Keep only the earliest free slot per technician.
    seen, result = set(), []
    for row in rows:
        if row["employee_id"] in seen:
            continue
        seen.add(row["employee_id"])
        row["date"] = str(row["date"])
        result.append(row)
    return result


# === SELF-TEST — python mcp_server/mcp_tools/read/list_available_technicians.py ===
if __name__ == "__main__":
    import json

    print("all available (earliest per tech):")
    print(json.dumps(list_available_technicians(), indent=2, default=str))
    print("\nfiltered to E13:", list_available_technicians(employee_id="E13"))