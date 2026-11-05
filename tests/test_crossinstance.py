"""Verify the disk-backed job model from a process that started no jobs.

This is the actual failure mode being fixed: Claude Desktop spawns a new server
instance, and that instance must still be able to report on jobs launched by an
earlier one. A fresh process with an empty JOBS dict is exactly that situation.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import server

print("default_wait_s (blocking budget) =", server.CFG["default_wait_s"])
print("fresh process JOBS dict         =", server.JOBS, "(empty = never launched anything)")
print()

print("=== jobs on disk, read cross-process ===")
for m in server._all_jobs():
    print(f"  {m['id']}  state={m['state']:<9} pid={m.get('pid')}  cwd={m.get('cwd')}")
print()

print("=== a job whose owner died, rendered by a stranger process ===")
# Pick a finished job from disk rather than naming one: job ids are per-machine.
_target = next((m for m in server._all_jobs() if m.get("state") != "running"), None)
if _target is None:
    print("  (no finished job on disk to render)")
else:
    print(f"  rendering {_target['id']}:")
    out = server._render_disk(_target["id"])
    for line in out.splitlines()[:12]:
        print("  " + line)
    print(f"  ...({len(out)} chars total)")
print()

print("=== dedupe: would a retry launch a second worker? ===")
for m in server._all_jobs():
    if m["state"] == "running":
        dup = server._find_duplicate(pathlib.Path(m["cwd"]), " ".join((m["task"] or "").split()))
        print(f"  {m['id']} running -> retry would reuse: {dup}")
