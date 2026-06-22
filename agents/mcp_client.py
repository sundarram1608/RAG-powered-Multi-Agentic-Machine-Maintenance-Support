"""
mcp_client.py — the bridge from the agents to the MCP tool servers.

Connects to BOTH MCP servers at once via MultiServerMCPClient (stdio for the 11
local-data tools + streamable-HTTP for the 2 service tools) and exposes them as
LangChain tools. `tools_for(agent)` filters that union down to each agent's
allow-list (config.AGENT_TOOLS) — this is what enforces per-agent least privilege.

Launch order: start the HTTP services server first
    python mcp_server/server.py http
then the stdio server is auto-spawned by this client.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # agents/ on path
import config

from langchain_mcp_adapters.client import MultiServerMCPClient

_client = MultiServerMCPClient(config.MCP_SERVERS)


def parse_tool_result(raw):
    """
    Normalize a LangChain-MCP tool result into the tool's native return value.

    The adapter delivers MCP tool output as a list of content blocks
    (`[{"type": "text", "text": "<json>"}]`); our tools return JSON. This unwraps
    that to the dict/list the tool actually returned.
    """
    if isinstance(raw, str):
        return json.loads(raw)
    if isinstance(raw, list) and raw and isinstance(raw[0], dict) and "text" in raw[0]:
        return json.loads(raw[0]["text"])
    return raw


async def get_all_tools():
    """All MCP tools (union of the stdio + HTTP servers) as LangChain tools."""
    return await _client.get_tools()


def tools_for(agent_name: str, all_tools) -> list:
    """Filter `all_tools` to the allow-list configured for `agent_name`."""
    allowed = set(config.AGENT_TOOLS.get(agent_name, []))
    return [tool for tool in all_tools if tool.name in allowed]


# ============================================================================
# MILESTONE TEST — Phase 4a. NOT part of the library; proves the agent<->MCP
# bridge before any node exists.
#   Run order:  python mcp_server/server.py http     # (separate terminal)
#               python agents/mcp_client.py
#   Part 1 (MCP connection + per-agent allow-lists) needs NO API key.
#   Part 2 (LLM-bound tool call) runs only if GROQ_API_KEY is set.
# ============================================================================
if __name__ == "__main__":

    async def _smoke_test():
        print("=== Part 1: MCP connection + per-agent tool allow-lists (no key needed) ===")
        tools = await get_all_tools()
        print(f"Connected to both servers — {len(tools)} tools: {sorted(t.name for t in tools)}\n")
        for agent in ("intake", "diagnosis", "technician_action", "self_action",
                      "analytics", "supervisor"):
            names = [t.name for t in tools_for(agent, tools)]
            print(f"  tools_for({agent!r:>20}) -> {names}")

        print("\n=== Part 2: LLM-bound tool call (needs GROQ_API_KEY) ===")
        if not config.GROQ_API_KEY:
            print("  skipped — set GROQ_API_KEY in .env to test a live LLM tool call.")
            return
        import llms
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = llms.get_reasoner().bind_tools(tools_for("intake", tools))
        reply = llm.invoke([
            SystemMessage(content="You validate machines for a maintenance agent. "
                                  "Use the get_machine tool to look one up."),
            HumanMessage(content="Look up machine M01."),
        ])
        print("  LLM tool_calls:", reply.tool_calls)

    asyncio.run(_smoke_test())
