"""
server.py — the MCP server(s) for the FDM maintenance agent.

Turns the plain tool functions in mcp_tools/ into MCP tools. Each function is
registered with FastMCP via add_tool(); FastMCP derives the tool's schema from
the function's NAME, DOCSTRING (-> the full description the LLM sees) and TYPE
HINTS (-> the input schema). The tool files stay decorator-free so they remain
standalone-testable; registration happens centrally here.

TWO TRANSPORTS, split by tool group (so the system uses both — see README):
    stdio : the local data plane — DB + RAG read/write tools, bundled with and
            auto-spawned by the agent.
    http  : the shared "services" — run_readonly_query (analytics) and send_email
            (notifications) — run as a separate localhost process the agent
            connects to over streamable-HTTP.

Run:
    python mcp_server/server.py            # stdio (default), local-data tools
    python mcp_server/server.py http       # streamable-HTTP on 127.0.0.1:8000/mcp
    python mcp_server/server.py --selftest  # list both groups' tools, then exit
"""

import argparse
import sys
from pathlib import Path

# Put mcp_tools/ on the path so its subpackages (read/, rag_wrappers/, write/,
# other/) and _common import cleanly.
sys.path.insert(0, str(Path(__file__).resolve().parent / "mcp_tools"))

from read.get_machine import get_machine
from read.get_overdue_status import get_overdue_status
from read.get_maintenance_history import get_maintenance_history
from read.get_incident_history import get_incident_history
from read.get_incident import get_incident
from read.list_incidents import list_incidents
from read.check_inventory import check_inventory
from read.find_available_technician import find_available_technician
from read.list_available_technicians import list_available_technicians
from read.list_machine_versions import list_machine_versions
from rag_wrappers.user_manual_retrieval import user_manual_retrieval
from rag_wrappers.safety_retrieval import safety_retrieval
from write.create_incident import create_incident
from write.book_technician_slot import book_technician_slot
from write.update_incident import update_incident
from other.run_readonly_query import run_readonly_query
from other.send_email import send_email

from mcp.server.fastmcp import FastMCP

# --- tool groups, split by transport ---
LOCAL_DATA_TOOLS = [
    get_machine, get_overdue_status, get_maintenance_history,
    get_incident_history, get_incident, list_incidents, check_inventory,
    find_available_technician, list_available_technicians, list_machine_versions,
    user_manual_retrieval, safety_retrieval,
    create_incident, book_technician_slot, update_incident,
]
SERVICE_TOOLS = [run_readonly_query, send_email]

HTTP_HOST = "127.0.0.1"
HTTP_PORT = 8000


def build_server(transport: str) -> FastMCP:
    """Create a FastMCP server registered with the tool group for `transport`."""
    if transport == "http":
        mcp = FastMCP("fdm-maintenance-services-agentic-ai",
                      host=HTTP_HOST, port=HTTP_PORT)
        tools = SERVICE_TOOLS
    else:  # stdio
        mcp = FastMCP("fdm-maintenance-tools-agentic-ai")
        tools = LOCAL_DATA_TOOLS
    for fn in tools:
        mcp.add_tool(fn)
    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="FDM maintenance MCP server")
    parser.add_argument("transport", nargs="?", default="stdio",
                        choices=["stdio", "http"],
                        help="stdio (default) = local data tools; http = services")
    parser.add_argument("--selftest", action="store_true",
                        help="list the tools each transport exposes, then exit")
    args = parser.parse_args()

    if args.selftest:
        _selftest()
        return

    mcp = build_server(args.transport)
    if args.transport == "http":
        # Serves the MCP endpoint at http://127.0.0.1:8000/mcp
        mcp.run(transport="streamable-http")
    else:
        mcp.run()  # stdio transport (default)


# ============================================================================
# SMOKE TEST — NOT part of the running server. Lists the tools each transport
# would expose and shows their parameters + one-line purpose, to verify the
# docstring->description and type-hint->schema wiring (no LLM, no network):
#     python mcp_server/server.py --selftest
# Note: the LLM receives each tool's FULL docstring as its description; only the
# first line is printed here to keep the listing readable.
# ============================================================================
def _selftest() -> None:
    import asyncio

    for transport, label in (("stdio", "STDIO — local data plane"),
                             ("http", "HTTP — shared services")):
        srv = build_server(transport)
        tools = asyncio.run(srv.list_tools())
        print(f"\n{label}  [{srv.name}]: {len(tools)} tools")
        for tool in tools:
            params = list((tool.inputSchema or {}).get("properties", {}).keys())
            desc = (tool.description or "").strip()
            first_line = desc.splitlines()[0] if desc else ""
            print(f"  - {tool.name}({', '.join(params)})")
            print(f"      {first_line}")


if __name__ == "__main__":
    main()
