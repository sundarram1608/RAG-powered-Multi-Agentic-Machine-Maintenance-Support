"""
test_routing.py — deterministic checks of every conditional-edge router in graph.py.

No LLM, no MCP, no DB — pure functions over representative states. This validates
the topology's branch logic (supervisor route, analytics/verify loops + caps,
needs_technician gate, decider/self_action branches) independently of model calls.
    python agents/test_routing.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # agents/
import config
from graph import (route_after_analytics_execute, route_after_decider,
                   route_after_input, route_after_manage_resolve, route_after_reviewer,
                   route_after_self_action, route_after_supervisor, route_after_verifier)

A = config.ANALYTICS_MAX_ATTEMPTS
V = config.VERIFY_MAX_ATTEMPTS

CASES = [
    # router, state, expected
    (route_after_input, {"input_safe": False}, "output"),
    (route_after_input, {"input_safe": True}, "supervisor"),

    (route_after_supervisor, {"intent": "troubleshoot"}, "intake"),
    (route_after_supervisor, {"intent": "analytics"}, "analytics_generate"),
    (route_after_supervisor, {"intent": "manage_incident"}, "manage_resolve"),
    (route_after_supervisor, {"intent": "general"}, "output"),

    (route_after_reviewer, {"sql_review": {"approved": True}}, "analytics_execute"),
    (route_after_reviewer, {"sql_review": {"approved": False}, "analytics_attempts": 1}, "analytics_generate"),
    (route_after_reviewer, {"sql_review": {"approved": False}, "analytics_attempts": A}, "output"),

    (route_after_analytics_execute, {"sql_result": {"ok": True}}, "output"),
    (route_after_analytics_execute, {"sql_result": {"ok": False}, "analytics_attempts": 1}, "analytics_generate"),
    (route_after_analytics_execute, {"sql_result": {"ok": False}, "analytics_attempts": A}, "output"),

    (route_after_manage_resolve, {"manage_plan": {"action": "unsupported"}}, "output"),
    (route_after_manage_resolve, {"manage_plan": {"action": "cancelled"}}, "output"),
    (route_after_manage_resolve, {"manage_plan": {"action": "close"}}, "manage_execute"),

    # verifier: approved + gate, reject loop, reject exhausted
    (route_after_verifier, {"verdict": {"approved": True}, "diagnosis": {"needs_technician": True}}, "technician_action"),
    (route_after_verifier, {"verdict": {"approved": True}, "diagnosis": {"needs_technician": False}}, "decider"),
    (route_after_verifier, {"verdict": {"approved": False}, "verify_attempts": 1}, "diagnosis"),
    (route_after_verifier, {"verdict": {"approved": False}, "verify_attempts": V}, "technician_action"),

    (route_after_decider, {"decision_path": "self"}, "self_action"),
    (route_after_decider, {"decision_path": "technician"}, "technician_action"),

    (route_after_self_action, {"action_result": {"action": "escalate_to_technician"}}, "technician_action"),
    (route_after_self_action, {"action_result": {"action": "self_resolved"}}, "output"),
    (route_after_self_action, {"action_result": {"action": "error"}}, "output"),
]

if __name__ == "__main__":
    fails = 0
    for fn, state, expected in CASES:
        got = fn(state)
        ok = got == expected
        fails += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {fn.__name__:32} -> {got:18} (want {expected})")
    print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}  ({len(CASES)} cases)")
    sys.exit(1 if fails else 0)
