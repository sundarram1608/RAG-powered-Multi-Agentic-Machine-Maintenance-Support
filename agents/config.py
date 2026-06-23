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
REASONING_TEMPERATURE = 0.0
JUDGE_TEMPERATURE = 0.0
# Provider SDK retries with exponential backoff — absorbs transient Groq 503
# (over-capacity) and transient Gemini 429s. (A hard daily-quota 429 is not
# retried away — it needs a higher-quota model/tier.)
LLM_MAX_RETRIES = 5

# Dataset reference "today" — must match the data layer's REFERENCE_TODAY
# (seeded data is anchored to June 2026; the real date.today() is not used).
REFERENCE_TODAY = date(2026, 6, 16)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

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
                "analytics": ["run_readonly_query"],
                "text_to_sql_reviewer": [],
                "manage_incident": [
                                    "get_incident", "find_available_technician",
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
