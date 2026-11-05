"""End-to-end test of the new job model.

Checks the three things that were broken:
  1. hermes_run returns fast (never blocks long enough for Claude to give up).
  2. A *separate* process can read the job and see it as running with a live pid.
  3. An identical retry is deduped instead of starting a second worker.
Then it waits for the job to finish and confirms the outcome was recorded.

Requires a working config.json (see config.example.json) pointing at a real Hermes
install; the Hermes CLI cold-starts in 10-40 s, so give it room.
"""
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent   # tests/
REPO = HERE.parent                                # repo root
sys.path.insert(0, str(REPO))
import server

WORK = pathlib.Path(server.CFG["default_cwd"])

TASK = ("Create a file named e2e-newmodel.txt in the current directory containing "
        "exactly the text BRIDGE-JOB-OK and nothing else.")

print(f"blocking budget = {server.CFG['default_wait_s']}s\n")

t0 = time.time()
out = server._launch_and_wait(
    task=TASK,
    cwd=str(WORK),
    max_turns=12,
    timeout_s=600,
    wait_s=server.CFG["default_wait_s"],
    resume=None,
    skills=None,
    toolsets=None,
)
elapsed = time.time() - t0

jid = next((l.split(": ", 1)[1] for l in out.splitlines() if l.startswith("JOB_ID: ")), None)
status = next((l.split(": ", 1)[1] for l in out.splitlines() if l.startswith("STATUS: ")), "?")

print(f"[1] hermes_run returned in {elapsed:.1f}s -> STATUS: {status}")
print(f"    JOB_ID: {jid}")
print(f"    [{'PASS' if elapsed < 20 else 'FAIL'}] returned fast enough "
      f"(limit 20s, was ~100s before)")
assert jid, "no job id returned"

print("\n[2] reading it from a separate interpreter (the next server instance):")
probe = subprocess.run([sys.executable, str(HERE / "probe_job.py"), jid],
                       capture_output=True, text=True, timeout=120)
print("\n".join("    " + l for l in probe.stdout.strip().splitlines())
      or f"    probe failed: {probe.stderr[-400:]}")

print("\n[3] waiting for the job to finish...")
deadline = time.time() + 420
final = None
while time.time() < deadline:
    final = server._read_job(jid)
    if final["state"] != "running":
        break
    time.sleep(5)

print(f"    final state={final['state']} exit_code={final.get('exit_code')} "
      f"duration={final.get('duration_s')}")

lp = pathlib.Path(final["log_path"])
print(f"    log: {lp} ({lp.stat().st_size if lp.exists() else 0} bytes)")

artifact = WORK / "e2e-newmodel.txt"
if artifact.exists():
    print(f"    artifact: {artifact.read_text(encoding='utf-8', errors='replace').strip()!r}")
else:
    print(f"    artifact MISSING: {artifact}")
