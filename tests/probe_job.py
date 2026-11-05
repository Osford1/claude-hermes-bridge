"""Read a job from a *separate* interpreter - the "next server instance" case."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import server

jid = sys.argv[1]
print(f"[other proc] JOBS dict here: {server.JOBS}  <- started nothing")
m = server._read_job(jid)
if m is None:
    print(f"[other proc] no such job {jid}")
    raise SystemExit(1)
print(f"[other proc] {jid}: state={m['state']} pid={m['pid']} "
      f"pid_alive={server._pid_alive(m['pid'])}")
dup = server._find_duplicate(pathlib.Path(m["cwd"]), " ".join((m["task"] or "").split()))
print(f"[other proc] an identical retry would reuse: {dup}")
