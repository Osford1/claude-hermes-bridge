#!/usr/bin/env python
"""Smoke-test the hermes-mcp server over a real stdio MCP connection.

Usage:
    python test_bridge.py tools                       # list tools
    python test_bridge.py health                      # hermes_health (instant)
    python test_bridge.py run "<task>" [cwd] [wait_s] # a real hermes_run
    python test_bridge.py poll <job_id> [wait_s]      # hermes_status
    python test_bridge.py call <tool> '<json args>'   # any tool, raw
"""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent
PY = sys.executable  # the interpreter running this test
SERVER = str(HERE.parent / "server.py")


def _show(res) -> None:
    for block in res.content:
        if getattr(block, "type", None) == "text":
            print(block.text)


async def main() -> int:
    params = StdioServerParameters(command=PY, args=[SERVER], env=None)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mode = sys.argv[1] if len(sys.argv) > 1 else "tools"

            if mode == "tools":
                listed = await session.list_tools()
                print(f"{len(listed.tools)} tools registered:\n")
                for t in listed.tools:
                    props = list(t.inputSchema.get("properties", {}).keys())
                    req = t.inputSchema.get("required", [])
                    print(f"  {t.name}({', '.join(props)})")
                    if req:
                        print(f"      required: {req}")
                return 0

            if mode == "health":
                _show(await session.call_tool("hermes_health", {}))
                return 0

            if mode == "run":
                args = {"task": sys.argv[2]}
                if len(sys.argv) > 3 and sys.argv[3]:
                    args["cwd"] = sys.argv[3]
                if len(sys.argv) > 4:
                    args["wait_s"] = int(sys.argv[4])
                _show(await session.call_tool("hermes_run", args))
                return 0

            if mode == "poll":
                args = {"job_id": sys.argv[2]}
                if len(sys.argv) > 3:
                    args["wait_s"] = int(sys.argv[3])
                _show(await session.call_tool("hermes_status", args))
                return 0

            if mode == "call":
                tool = sys.argv[2]
                args = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
                _show(await session.call_tool(tool, args))
                return 0

            print(f"unknown mode {mode!r}", file=sys.stderr)
            return 2

    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
