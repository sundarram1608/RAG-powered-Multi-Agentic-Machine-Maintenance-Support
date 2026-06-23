"""
run.py — interactive CLI driver for the agent workflow (manual end-to-end testing).

Prereq: start the HTTP services server first, then run this:
    python mcp_server/server.py http     # separate terminal
    python agents/run.py

One process = one conversation (thread). When the bot pauses for input (clarify /
decision / choice / approve) your next line is sent as the resume value; otherwise
it starts a fresh request. Type 'quit' to exit.
"""

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # agents/ on path
from api import resume_turn, start_turn


async def main():
    thread = f"cli-{uuid.uuid4().hex[:8]}"
    user_id = sys.argv[1] if len(sys.argv) > 1 else "E01"
    print(f"Agentic FDM Services — thread={thread} user={user_id}. Type 'quit' to exit.\n")

    paused = False
    while True:
        try:
            msg = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if msg.lower() in ("quit", "exit"):
            break
        if not msg:
            continue

        res = await (resume_turn(thread, msg) if paused else start_turn(thread, user_id, msg))
        if res["kind"] == "answer":
            print(f"\nbot> {res['content']}\n")
            paused = False
        else:
            p = res["payload"]
            prompt = p.get("question") or p.get("guidance") or p.get("summary") or ""
            opts = p.get("options")
            print(f"\nbot> [{res['kind']}] {prompt}" + (f"  options={opts}" if opts else "") + "\n")
            paused = True


if __name__ == "__main__":
    asyncio.run(main())
