"""
check_inventory — stock / availability / bin / compatibility for a part.

Used by: Diagnosis & Action (is the needed part available before a fix/booking?).
Matches by exact part_id or a fuzzy part_name (LIKE).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # .../mcp_tools
from _common import run_query


def check_inventory(part: str) -> list:
    """
    Check stock, location, and machine-compatibility of a spare part. Call this
    BEFORE recommending a part-replacement fix or booking a repair, so the
    recommendation reflects what is actually on the shelf.

    Args:
        part: A part id ("PRT-001") for an exact match, OR a name fragment
              ("thermistor", "nozzle") for a fuzzy search that may return several
              matching parts.

    Returns:
        [{part_id, part_name, category, compatible_mvc, quantity_on_hand,
          reorder_threshold, unit, bin_location,
          in_stock,   # True if quantity_on_hand > 0
          low_stock   # True if quantity_on_hand <= reorder_threshold (reorder soon)
         }, ...]
        []   # no part matched — try a different name or part id
    """
    part = (part or "").strip()
    rows = run_query(
        """
        SELECT part_id, part_name, category, compatible_mvc,
               quantity_on_hand, reorder_threshold, unit, bin_location
        FROM inventory
        WHERE part_id = %s OR part_name LIKE %s
        """,
        (part, f"%{part}%"),
    )
    for row in rows:
        row["in_stock"] = row["quantity_on_hand"] > 0
        row["low_stock"] = row["quantity_on_hand"] <= row["reorder_threshold"]
    return rows


# === SELF-TEST — python mcp_server/mcp_tools/read/check_inventory.py ===
if __name__ == "__main__":
    import json

    print(json.dumps(check_inventory("thermistor"), indent=2, default=str))
    print(json.dumps(check_inventory("PRT-002"), indent=2, default=str))  # out of stock
