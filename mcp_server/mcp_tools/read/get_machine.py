"""
get_machine — validate a machine exists and resolve its version/status/location.

Used by: Intake (validate machine_id), Diagnosis (resolve mvc_code for RAG).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # .../mcp_tools
from _common import run_query


def get_machine(machine_id: str) -> dict:
    """
    Validate that a machine exists and resolve its identity before any
    troubleshooting. Call this FIRST whenever a user names a machine — every
    other tool and the RAG manual lookup needs the resolved `mvc_code` (machine
    version) this returns. Also reveals operational status, so the agent can
    refuse to troubleshoot a Decommissioned unit.

    Args:
        machine_id: Machine tag like "M01" (case-insensitive; "m01" is accepted).

    Returns:
        {exists: True, machine_id, mvc_code, model_name, status, location}
        {exists: False, machine_id}   # unknown tag -> ask the user to re-confirm it
    """
    machine_id = (machine_id or "").strip().upper()
    rows = run_query(
        """
        SELECT m.machine_id, m.mvc_code, m.status, m.location, v.model_name
        FROM machines m
        JOIN machine_versions v ON m.mvc_code = v.mvc_code
        WHERE m.machine_id = %s
        """,
        (machine_id,),
    )
    if not rows:
        return {"exists": False, "machine_id": machine_id}
    machine = rows[0]
    machine["exists"] = True
    return machine


# === SELF-TEST — python mcp_server/mcp_tools/read/get_machine.py ===
if __name__ == "__main__":
    print("M01           ->", get_machine("M01"))
    print("m03 (lower)   ->", get_machine("m03"))
    print("M99 (missing) ->", get_machine("M99"))
