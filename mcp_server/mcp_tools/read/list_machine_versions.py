"""
list_machine_versions — list every machine version (model) the plant runs.

Read-only: returns one row per machine version from `machine_versions` (the single
source of truth), so a caller can iterate the models without hardcoding them.

Used by: the Advice agent — it is machine-agnostic, so to answer a how-to across the
whole fleet it lists the versions, then retrieves each model's manual (via
user_manual_retrieval) and composes one shared answer + per-model deltas.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # .../mcp_tools
from _common import run_query


def list_machine_versions() -> list:
    """
    List all machine versions (models) in the fleet — the models the assistant
    supports. Use this when you need to cover every model (e.g. machine-agnostic
    guidance), rather than one specific machine.

    Returns (one row per version, ordered by mvc_code):
        [{mvc_code, model_name, machine_type, manufacturer}, ...]
    """
    return run_query(
        "SELECT mvc_code, model_name, machine_type, manufacturer "
        "FROM machine_versions ORDER BY mvc_code"
    )


# === SELF-TEST — python mcp_server/mcp_tools/read/list_machine_versions.py ===
if __name__ == "__main__":
    import json

    print(json.dumps(list_machine_versions(), indent=2, default=str))
