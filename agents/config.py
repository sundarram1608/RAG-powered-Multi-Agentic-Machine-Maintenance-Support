"""
config.py — single source of truth for the agent layer.

Holds the model choices, the MCP server connection map (both transports), the
per-agent tool allow-lists (least privilege — each agent sees ONLY its tools),
and workflow constants. Loads API keys from the project-root .env.
"""

import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=True)

# ── Models (free tiers; API-based, no local hosting) ──
REASONING_MODEL = "llama-3.3-70b-versatile"   # Groq — reasoning / tool-calling
JUDGE_MODEL = "gemini-2.5-flash-lite"         # Google — independent verifier (higher free RPD than 2.5-flash)
JUDGE_FALLBACK_MODEL = "qwen/qwen3-32b"       # Groq — fallback judge when Gemini is unavailable
                                              # (transient 503/quota); a different family than the Llama reasoner
REASONING_TEMPERATURE = 0.0
JUDGE_TEMPERATURE = 0.0
# Per-key SDK retries (exponential backoff) for the reasoner. Kept LOW: 5 retries on a
# hard daily-cap 429 is ~15s of pointless backoff per key (×2 keys with failover ≈ 30s
# of "Working…") since a cap won't clear in seconds. 2 still smooths a transient 503
# blip, and _QuotaFailover handles the rest (try the backup key, else error fast with
# the friendly message). The judge chain fails even faster (JUDGE_MAX_RETRIES).
LLM_MAX_RETRIES = 2
# The JUDGE (Gemini) has a cross-family fallback to Qwen-on-Groq, so when Gemini is
# down (503 high-demand) we want to FAIL OVER FAST rather than retry a struggling
# provider 5x per key (which made a turn hang for ~a minute). One quick retry, then
# the _QuotaFailover chain advances to the next key / to Qwen.
JUDGE_MAX_RETRIES = 1

# Dataset reference "today" — must match the data layer's REFERENCE_TODAY
# (seeded data is anchored to June 2026; the real date.today() is not used).
REFERENCE_TODAY = date(2026, 6, 16)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
# Optional SECONDARY keys (same provider). When set, llms.py fails over to them on a
# rate-limit / quota / capacity error so a turn can still finish. NOTE: Groq's
# free-tier token cap is per-ACCOUNT, so a second key from the same account shares
# that cap — failover adds headroom only across separate accounts (or per-key limits).
GROQ_API_KEY_2 = os.getenv("GROQ_API_KEY_2")
GOOGLE_API_KEY_2 = os.getenv("GOOGLE_API_KEY_2")

# Substrings that mark a provider "can't serve this now" error — a daily/rate cap
# (429, quota) or transient capacity (503, overloaded). Used by api.py for the
# user-facing message AND by llms.py to decide when to fail over to a backup key.
# It deliberately does NOT match request/validation bugs (400, bad schema), so those
# surface immediately instead of being silently retried on another key.
RATE_LIMIT_HINTS = (
    "rate limit", "ratelimit", "429", "resource_exhausted", "tokens per day",
    "tokens per minute", "quota", "too many requests",
    "over capacity", "503", "service unavailable", "overloaded",
)


def is_rate_limit_error(exc: BaseException) -> bool:
    """True if `exc` looks like a quota/rate/capacity error (see RATE_LIMIT_HINTS).
    Used for the user-facing "free-tier limit" message — keep it quota-specific."""
    s = str(exc).lower()
    return any(h in s for h in RATE_LIMIT_HINTS)


# Transient network/connection blips — also infra failures (not request/validation
# bugs), so the judge should fail over to its backup on these too. Kept SEPARATE from
# RATE_LIMIT_HINTS so the "free-tier limit" message stays accurate (a timeout is not a cap).
CONNECTION_HINTS = (
    "connection", "connecterror", "timeout", "timed out", "remote disconnected",
    "connection reset", "connection aborted", "temporarily unavailable", "network",
    "max retries exceeded", "eof occurred", "server disconnected", "read timed out",
)


def is_transient_error(exc: BaseException) -> bool:
    """True for any transient infra failure worth failing over on — a rate/quota/
    capacity error OR a connection/timeout blip. NOT request/validation bugs."""
    if is_rate_limit_error(exc):
        return True
    s = str(exc).lower()
    return any(h in s for h in CONNECTION_HINTS)

# ── MCP servers (the agents connect to BOTH at once) ──
#   local_data : stdio  — auto-spawned; the 13 read/RAG/write tools
#   services   : HTTP   — separate process; run_readonly_query + send_email
MCP_SERVERS = {
                "local_data": {
                                "command": sys.executable,                       # the venv's python
                                "args": [str(PROJECT_ROOT / "mcp_server" / "server.py"), "stdio"],
                                "transport": "stdio",
                            },
                "services": {
                                "url": "http://127.0.0.1:8000/mcp",
                                "transport": "streamable_http",
                            },
            }

# ── Per-agent tool allow-lists (least privilege) ──
# Each agent is bound ONLY to the tools below — mirrors the least-privilege DB
# users. Empty list = a pure reasoning/routing agent with no tools.
AGENT_TOOLS = {
                "input": [],
                "supervisor": [],
                "advice": ["list_machine_versions", "user_manual_retrieval", "safety_retrieval"],
                "analytics": ["run_readonly_query"],
                "text_to_sql_reviewer": [],
                "manage_incident": [
                                    "get_incident", "list_incidents", "find_available_technician",
                                    "list_available_technicians", "book_technician_slot",
                                    "update_incident", "send_email",
                                ],
                "intake": ["get_machine"],
                "diagnosis": [
                                "user_manual_retrieval", "safety_retrieval", "get_overdue_status",
                                "get_maintenance_history", "get_incident_history", "check_inventory",
                            ],
                "verifier": [],
                "decider": [],
                "self_action": ["create_incident", "update_incident"],
                "technician_action": [
                                        "find_available_technician", "create_incident", "book_technician_slot",
                                        "update_incident", "send_email",
                                    ],
                "output": [],
            }

# ── Workflow constants ──
VERIFY_MAX_ATTEMPTS = 3        # verifier -> diagnosis self-correction cap
MAX_DIAGNOSIS_REQUERIES = 3    # corrective-RAG re-query cap inside diagnosis
ANALYTICS_MAX_ATTEMPTS = 3     # text-to-SQL coder<->reviewer / DB-error retry cap
