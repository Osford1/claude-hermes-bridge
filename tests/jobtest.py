#!/usr/bin/env python
"""End-to-end test of the background-job model, inside ONE server session.

Proves: a short wait_s returns STATUS: running (not a failure, not a kill),
the job keeps running, and hermes_status later collects the real result.
"""
import asyncio
import re
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent
PY = sys.executable
SERVER = str(HERE.parent / "server.py")

TARGET = str(Path(tempfile.gettempdir()) / "job-model-test.txt")
TASK = (
    f"Create the file {TARGET} whose complete contents are exactly the single line "
    "JOB-MODEL-OK with no trailing text. Then read the file back and print its contents. "
    f"Done means {TARGET} exists and contains that exact line."
)


def text_of(res) -> str:
    return "\n".join(b.text for b in res.content if getattr(b, "type", None) == "text")


async def main() -> int:
    params = StdioServerParameters(command=PY, args=[SERVER], env=None)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print("### 1. hermes_run with wait_s=3 (deliberately shorter than the task)")
            r1 = text_of(await session.call_tool("hermes_run", {"task": TASK, "wait_s": 3}))
            print(r1)
            m = re.search(r"JOB_ID: (\S+)", r1)
            if not m:
                print("\n!! no JOB_ID returned — the job model did not engage")
                return 1
            job = m.group(1)
            first_status = re.search(r"STATUS: (\w+)", r1).group(1)

            print(f"\n### 2. hermes_jobs while it should still be running")
            print(text_of(await session.call_tool("hermes_jobs", {})))

            print(f"\n### 3. hermes_status(job_id={job}, wait_s=180) — poll to completion")
            r3 = text_of(await session.call_tool("hermes_status", {"job_id": job, "wait_s": 180}))
            print(r3)
            final_status = re.search(r"STATUS: (\w+)", r3).group(1)

            print("\n### VERDICT")
            print(f"  first call status : {first_status}  (want: running)")
            print(f"  final call status : {final_status}  (want: ok)")
            ok = first_status == "running" and final_status == "ok"
            print(f"  job model         : {'PASS' if ok else 'FAIL'}")
            return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
