#!/usr/bin/env python
"""
hermes-mcp — a local stdio MCP server that gives Claude Desktop an executor.

Claude plans and verifies; this server hands the actual work to Hermes Agent
(DeepSeek-backed, with shell/file/browser tools) and returns raw evidence.

Design rules, in order of importance:
  1. stdout is reserved for JSON-RPC. Nothing else may ever write to it.
     All diagnostics go to stderr. A stray print() breaks the transport.
  2. Claude Desktop kills any tool call that runs past its own timeout
     (default 180 s, from the app's `mcpToolTimeoutMs`). Therefore NO tool
     here may block for long: long work runs as a background JOB that is
     polled. This is the whole reason for the job model below.
  3. Every call echoes the exact command it ran, so Claude's plan stays auditable.
  4. A timeout is reported as a timeout, never as an empty success.
  5. cwd is confined to an allow-list; an MCP tool is an unattended shell.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
LOG_DIR = HERE / "logs"
JOB_DIR = HERE / "jobs"

DEFAULTS = {
    "hermes_exe": r"C:\Users\<you>\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes.exe",
    "allowed_roots": [
        r"C:\Users\<you>\Sessions",
        r"C:\Users\<you>\Projects",
        r"C:\Users\<you>\Desktop",
        r"C:\Users\<you>\Downloads",
        r"C:\Users\<you>\Documents",
        r"C:\Users\<you>\claude-work",
        r"C:\Temp",
    ],
    "default_cwd": r"C:\Users\<you>\claude-work",
    "default_max_turns": 40,
    # Total wall-clock allowed for one task. Enforced by us, not by the client.
    "default_timeout_s": 1800,
    # How long a *blocking* call may hold the client before returning a job id.
    # Must stay comfortably under Claude Desktop's 180 s tool timeout.
    "default_wait_s": 5,
    "max_output_chars": 24000,
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception as exc:
            print(f"[hermes-mcp] config.json unreadable ({exc}); using defaults", file=sys.stderr)
    return cfg


CFG = load_config()
LOG_DIR.mkdir(parents=True, exist_ok=True)
JOB_DIR.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------
# What Claude is told about Hermes at connect time.
#
# The model sees seven tool names and would assume a generic shell.
# The skill library is the whole point of Hermes, so it has to be visible.
# --------------------------------------------------------------------------

INSTRUCTIONS = """
This server executes work on a Windows workstation via Hermes Agent, a general
agent with shell, file, and browser tools plus a large library of procedural
skills (several hundred, grouped by category: cad, creative, email, finance,
github, mlops, note-taking, productivity, research, science, software-development,
and more).

Division of labour: you plan and verify; Hermes executes. Hermes runs unattended
and cannot ask you questions, so every task must be self-contained with an explicit
statement of what "done" looks like and absolute paths.

Call hermes_capabilities() to see what Hermes can actually do before planning a
complex task — it has skills for things you would otherwise hand-roll, and knowing
that changes the plan. Call hermes_capabilities(query="latex") to filter.

Nothing blocks. hermes_run returns within seconds with either the result or
STATUS: running plus a JOB_ID. That is normal, not a failure: call
hermes_status(job_id) until the status is no longer running. Never re-issue a task
that is already running - a duplicate would edit the same files concurrently.
hermes_jobs() lists everything currently in flight.

Verification is your job: a tool reporting success is not evidence. Use
hermes_read to inspect the artifact yourself.

Budget: the Hermes CLI cold-starts in 10-40 s before doing any work, so a task
that finishes in under a minute is normal even when trivial.
"""


mcp = FastMCP("hermes-executor", instructions=INSTRUCTIONS)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _log(msg: str) -> None:
    """stderr only — stdout belongs to the protocol."""
    print(f"[hermes-mcp {datetime.now():%H:%M:%S}] {msg}", file=sys.stderr, flush=True)


def _resolve_cwd(cwd: str | None) -> tuple[Path, str | None]:
    """Return (path, error). Path must sit inside one of the allowed roots."""
    raw = cwd or CFG["default_cwd"]
    try:
        path = Path(raw).expanduser().resolve()
    except Exception as exc:
        return Path(CFG["default_cwd"]), f"could not resolve cwd {raw!r}: {exc}"

    for root in CFG["allowed_roots"]:
        try:
            root_p = Path(root).resolve()
        except Exception:
            continue
        if path == root_p or root_p in path.parents:
            return path, None
    return path, (
        f"cwd {path} is outside the allow-list. Allowed roots:\n"
        + "\n".join(f"  - {r}" for r in CFG["allowed_roots"])
    )


def _installed_version() -> str | None:
    """Read the Hermes version from disk. Instant — never spawns the CLI."""
    try:
        venv = Path(CFG["hermes_exe"]).parent.parent
        site = venv / "Lib" / "site-packages"
        for d in sorted(site.glob("hermes_agent-*.dist-info")):
            return d.name[len("hermes_agent-"):-len(".dist-info")]
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------
# Capability index — what Hermes can do, read from disk (never spawns the CLI)
# --------------------------------------------------------------------------

def _skill_roots() -> list[Path]:
    roots = []
    try:
        roots.append(Path(CFG["hermes_exe"]).parent.parent.parent / "skills")  # builtin
    except Exception:
        pass
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "hermes" / "skills")                        # this profile
    return [r for r in roots if r.exists()]


def _parse_frontmatter(text: str) -> dict:
    """Minimal YAML frontmatter reader — only what a SKILL.md header needs."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    out: dict[str, str] = {}
    key = None
    for raw in text[3:end].splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line[:1] in " \t" and key:              # folded continuation line
            out[key] = (out[key] + " " + line.strip()).strip()
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            key = k.strip()
            out[key] = v.strip().strip('"').strip("'")
    return out


def _skill_index() -> list[dict]:
    """Every SKILL.md under the active roots, de-duplicated by name."""
    skills: list[dict] = []
    seen: set[str] = set()
    for root in _skill_roots():
        for md in root.rglob("SKILL.md"):
            try:
                fm = _parse_frontmatter(md.read_text(encoding="utf-8", errors="replace")[:4000])
            except Exception:
                continue
            name = fm.get("name") or md.parent.name
            if name in seen:
                continue
            seen.add(name)
            parent = md.parent.parent
            cat = "" if parent == root else parent.name
            desc = " ".join(fm.get("description", "").split())
            skills.append({"name": name, "category": cat, "description": desc})
    return sorted(skills, key=lambda s: (s["category"], s["name"]))


# --------------------------------------------------------------------------
# Job machinery
#
# A job is a Hermes subprocess. Its output goes straight to jobs/<id>.log and
# its state to jobs/<id>.json. Keeping this on disk rather than in memory is
# deliberate: Claude Desktop starts a NEW server instance per session, so a job
# launched by one instance has to be readable by whichever instance asks about
# it next. Anything held only in this process is invisible to the one that needs
# it.
# --------------------------------------------------------------------------

JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_STILL_ACTIVE = 259


def _pid_alive(pid) -> bool:
    """Is that PID still running? Answerable from any process, not just the owner."""
    try:
        pid = int(pid or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
        k32.CloseHandle(handle)
        return bool(ok) and code.value == _STILL_ACTIVE
    except Exception:
        try:
            import psutil
            return psutil.pid_exists(pid)
        except Exception:
            return True        # cannot tell — do not claim a live job died


def _meta_path(job_id: str) -> Path:
    return JOB_DIR / f"{job_id}.json"


def _log_path(job_id: str) -> Path:
    return JOB_DIR / f"{job_id}.log"


def _write_job_meta(job: dict, state: str) -> None:
    try:
        _meta_path(job["id"]).write_text(json.dumps({
            "id": job["id"], "state": state, "task": job["task"],
            "command": job["cmd"], "cwd": job["workdir"], "pid": job.get("pid"),
            "log": str(_log_path(job["id"])),
            "started": datetime.fromtimestamp(job["started"]).isoformat(timespec="seconds"),
            "timeout_s": job["timeout_s"], "exit_code": job.get("exit_code"),
            "duration_s": job.get("duration"),
        }, indent=2), encoding="utf-8")
    except Exception as exc:
        _log(f"meta write failed for {job['id']}: {exc}")


def _read_job(job_id: str) -> dict | None:
    """Job state from disk -- valid from any server instance.

    If the recorded process is gone but no exit code was ever written, the owning
    server died mid-job: report 'orphaned' rather than 'running' forever.
    """
    path = _meta_path(job_id)
    if not path.exists():
        return None
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    meta["log_path"] = str(_log_path(job_id))
    if meta.get("state") == "running":
        pid = meta.get("pid")
        if pid is None:
            # Job files written before pid tracking. The process may well still be
            # alive, so do not claim it died -- that invites a duplicate re-run.
            meta["state"] = "running_untracked"
        elif not _pid_alive(pid):
            meta["state"] = "orphaned"
    return meta


def _all_jobs() -> list[dict]:
    """Every job on disk, oldest first, with liveness re-checked."""
    jobs = [m for m in (_read_job(p.stem) for p in sorted(JOB_DIR.glob("*.json"))) if m]
    return sorted(jobs, key=lambda m: m.get("started") or "")


def _find_duplicate(workdir: Path, norm_task: str) -> str | None:
    """An already-running job doing exactly this, from any instance.

    Claude re-issues a call it believes failed. Without this guard a retry starts
    a second worker in the same directory -- two agents editing one tree.
    """
    for meta in reversed(_all_jobs()):
        if (meta.get("state") == "running"
                and meta.get("cwd") == str(workdir)
                and " ".join((meta.get("task") or "").split()) == norm_task
                and _pid_alive(meta.get("pid"))):
            return meta["id"]
    return None


def _start_job(task, workdir, max_turns, timeout_s, resume, skills, toolsets) -> dict:
    cmd = [
        CFG["hermes_exe"],
        "chat",
        "--query-file", "-",      # task on stdin: nothing is shell-interpreted
        "-Q",                     # quiet: final response + session info only
        "--oneshot",
        "--in", str(workdir),
        "--max-turns", str(int(max_turns)),
    ]
    if resume:
        cmd += ["--resume", resume]
    if skills:
        cmd += ["--skills", skills]
    if toolsets:
        cmd += ["--toolsets", toolsets]

    job_id = datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{len(JOBS) + 1}"
    lp = _log_path(job_id)

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    # Output goes straight to a file rather than a pipe: no reader threads to
    # deadlock, and nothing is lost when this process goes away.
    log_fh = open(lp, "w", encoding="utf-8", errors="replace", buffering=1)
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(workdir), env=env,
            stdin=subprocess.PIPE, stdout=log_fh, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", shell=False,
        )
    except Exception:
        log_fh.close()
        raise

    try:
        proc.stdin.write(task)
        proc.stdin.flush()
        proc.stdin.close()
    except Exception as exc:
        _log(f"job {job_id} stdin write failed: {exc}")

    job = {
        "id": job_id, "proc": proc, "pid": proc.pid, "cmd": cmd,
        "workdir": str(workdir), "task": task, "started": time.time(),
        "timeout_s": int(timeout_s), "log_path": str(lp),
        "status": "running", "exit_code": None, "duration": None,
    }
    with _JOBS_LOCK:
        JOBS[job_id] = job
    _write_job_meta(job, state="running")
    _log(f"job {job_id} START pid={proc.pid}: "
         f"{' '.join(shlex.quote(c) for c in cmd)} (cwd={workdir}, timeout={timeout_s}s)")
    threading.Thread(target=_monitor, args=(job, log_fh), daemon=True).start()
    return job


def _close(fh) -> None:
    try:
        fh.close()
    except Exception:
        pass


def _monitor(job: dict, log_fh) -> None:
    """Watch the job detached, enforce our own hard cap, record the outcome.

    Detached on purpose: the outcome still lands on disk after the tool call has
    already returned STATUS: running.
    """
    proc = job["proc"]
    deadline = job["started"] + job["timeout_s"]
    while True:
        try:
            code = proc.wait(timeout=5)
            break
        except subprocess.TimeoutExpired:
            if time.time() > deadline:
                _log(f"job {job['id']} OVERRUN at {job['timeout_s']}s -- killing")
                try:
                    proc.kill()
                except Exception:
                    pass
                job["status"] = "timeout"
                job["duration"] = time.time() - job["started"]
                _write_job_meta(job, state="timeout")
                try:
                    log_fh.write(f"\n[hermes-mcp] HARD KILLED at {job['timeout_s']}s -- "
                                 "the task may be part-done; inspect the cwd.\n")
                except Exception:
                    pass
                _close(log_fh)
                return

    job["exit_code"] = code
    job["duration"] = time.time() - job["started"]
    job["status"] = "ok" if code == 0 else "error"
    _log(f"job {job['id']} {job['status'].upper()} in {job['duration']:.1f}s (rc={code})")
    _write_job_meta(job, state=job["status"])
    _close(log_fh)


def _render_disk(job_id: str, prefix: str = "") -> str:
    """Render a job for Claude. Reads disk, so any instance can answer."""
    meta = _read_job(job_id)
    if meta is None:
        return f"STATUS: unknown_job\nREASON: no job {job_id!r}.\nJOB_DIR: {JOB_DIR}"

    state = meta.get("state", "running")
    lp = Path(meta["log_path"])
    text = lp.read_text(encoding="utf-8", errors="replace") if lp.exists() else ""

    try:
        elapsed = (datetime.now() - datetime.fromisoformat(meta["started"])).total_seconds()
    except Exception:
        elapsed = 0.0
    if meta.get("duration_s") is not None:
        elapsed = meta["duration_s"]

    parts = []
    if prefix:
        parts.append(prefix.rstrip())
    parts += [
        f"STATUS: {state}",
        f"JOB_ID: {job_id}",
        f"CWD: {meta.get('cwd')}",
        f"ELAPSED_S: {elapsed:.0f}",
        f"TIMEOUT_S: {meta.get('timeout_s')} (hard cap we enforce)",
        f"FULL_LOG: {lp}",
    ]

    if state in ("running", "running_untracked"):
        if state == "running_untracked":
            parts += [
                "",
                "NOTE: this job predates pid tracking, so its process could not be",
                "checked. Treat it as running until the working directory says otherwise.",
            ]
        parts += [
            "",
            "Still running. This is NOT a failure and NOT a timeout.",
            f'Collect the result with hermes_status(job_id="{job_id}").',
            "Do not re-run the task -- it is already executing.",
            "",
            "--- OUTPUT SO FAR (tail) ---",
            (text.strip()[-2000:] or "(nothing yet)"),
        ]
        return "\n".join(parts)

    if state == "orphaned":
        parts += [
            "",
            "ORPHANED: the server process that owned this job is gone, so no exit",
            "code was recorded, and the Hermes process is gone too. The work may have",
            "completed or stopped part-way -- read the log tail and check the working",
            "directory before deciding to re-run.",
        ]

    if state == "timeout":
        parts += [
            "",
            f"NOTE: hard-killed at {meta.get('timeout_s')}s. The task may be part-done --",
            "inspect the working directory before re-running.",
        ]

    if meta.get("exit_code") is not None:
        parts.append(f"EXIT_CODE: {meta['exit_code']}")

    limit = int(CFG["max_output_chars"])
    body = text.strip()
    if len(body) > limit:
        body = body[:limit] + f"\n...[truncated; full text in {lp}]"
    parts += ["", "--- HERMES OUTPUT ---", (body or "(no output)")]
    return "\n".join(parts)


def _launch_and_wait(task, cwd, max_turns, timeout_s, wait_s, resume, skills, toolsets) -> str:
    """Start a job, block for at most wait_s, hand back text for Claude."""
    workdir, err = _resolve_cwd(cwd)
    if err:
        return f"STATUS: refused\nREASON: {err}"
    if not workdir.exists():
        try:
            workdir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return f"STATUS: refused\nREASON: cwd {workdir} could not be created: {exc}"

    dup = None if resume else _find_duplicate(workdir, " ".join(task.split()))
    if dup:
        _log(f"job {dup} REUSED -- identical task already running (dedupe)")
        return _render_disk(dup, prefix="NOTE: an identical task was already running; "
                                        "reusing that job instead of starting a second one.\n")

    job = _start_job(task, workdir, max_turns, timeout_s, resume, skills, toolsets)

    deadline = time.time() + max(0, int(wait_s))
    while time.time() < deadline and job["status"] == "running":
        time.sleep(0.5)
    return _render_disk(job["id"])


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------

@mcp.tool()
def hermes_run(
    task: str,
    cwd: str = "",
    max_turns: int = 0,
    timeout_s: int = 0,
    wait_s: int = 0,
    skills: str = "",
    toolsets: str = "",
) -> str:
    """Execute a task with Hermes Agent, which has shell, file, and browser tools.

    Use this for any work that needs to actually happen on the machine: running
    code, writing files, compiling LaTeX, installing packages, fetching a page.

    Write the task as a complete, self-contained instruction. Include an explicit
    statement of what "done" looks like, because Hermes runs unattended and cannot
    ask you questions. State absolute paths.

    This returns within ~wait_s seconds (default 5) no matter how long the task
    takes. If the task is still going, you get STATUS: running plus a JOB_ID, and
    you collect the result later with hermes_status. That is normal, not an error —
    a hard timeout can never silently kill your work.

    Returns: STATUS (ok | error | timeout | running | refused), exit code, duration,
    working directory, path to the full raw log, and Hermes' final response.
    On STATUS: timeout the task may be part-done — check the working directory
    before re-running. This call is not idempotent; do not retry blindly.
    """
    return _launch_and_wait(
        task=task,
        cwd=cwd or None,
        max_turns=int(max_turns) or CFG["default_max_turns"],
        timeout_s=int(timeout_s) or CFG["default_timeout_s"],
        wait_s=int(wait_s) if int(wait_s) else int(CFG["default_wait_s"]),
        resume=None, skills=skills or None, toolsets=toolsets or None,
    )


@mcp.tool()
def hermes_resume(session_id: str, task: str, cwd: str = "", max_turns: int = 0,
                  timeout_s: int = 0, wait_s: int = 0) -> str:
    """Continue an earlier Hermes session, keeping its context and history.

    Cheaper and less error-prone than hermes_run when you are asking for a fix to
    work already in progress — for example correcting a compile error, or redoing
    a step whose output you rejected.

    session_id: the ID printed in a previous call's output, or "latest".
    Same background-job behaviour as hermes_run.
    """
    return _launch_and_wait(
        task=task,
        cwd=cwd or None,
        max_turns=int(max_turns) or CFG["default_max_turns"],
        timeout_s=int(timeout_s) or CFG["default_timeout_s"],
        wait_s=int(wait_s) if int(wait_s) else int(CFG["default_wait_s"]),
        resume=session_id, skills=None, toolsets=None,
    )


@mcp.tool()
def hermes_status(job_id: str, wait_s: int = 30) -> str:
    """Collect the result of a background job started by hermes_run.

    Call this after hermes_run returned STATUS: running. It waits up to wait_s
    seconds for the job to finish, then reports either the final result or
    STATUS: running again -- keep polling until it is no longer running.

    Use wait_s=0 for an instant check. State is read from disk, so this works
    from any session, including one that did not start the job.
    """
    deadline = time.time() + max(0, int(wait_s))
    while True:
        meta = _read_job(job_id)
        if meta is None:
            recent = ", ".join(m["id"] for m in _all_jobs()[-8:]) or "(none)"
            return (f"STATUS: unknown_job\nREASON: no job {job_id!r}.\n"
                    f"RECENT_JOBS: {recent}\nJOB_DIR: {JOB_DIR}")
        if meta.get("state") != "running" or time.time() >= deadline:
            return _render_disk(job_id)
        time.sleep(1.0)


@mcp.tool()
def hermes_jobs() -> str:
    """List background jobs and their states, oldest first. Cheap; reads from disk."""
    metas = _all_jobs()
    if not metas:
        return "STATUS: ok\nJOBS: (none)"
    lines = ["STATUS: ok", f"JOBS: {len(metas)}"]
    for m in metas:
        dur = m.get("duration_s")
        if dur is None:
            try:
                dur = (datetime.now() - datetime.fromisoformat(m["started"])).total_seconds()
            except Exception:
                dur = 0.0
        lines.append(f"  {m['id']}  {str(m.get('state')):9s} {dur:7.0f}s  cwd={m.get('cwd')}")
    return "\n".join(lines)


@mcp.tool()
def hermes_read(path: str, max_bytes: int = 200000) -> str:
    """Read a file or list a directory from this machine, without waking an agent.

    Use this to verify work claimed by a previous hermes_run, or to inspect an
    artifact before deciding what to do next. Reading the file yourself is the
    point: a tool reporting success is not evidence that the work is correct.
    """
    p = Path(path).expanduser()
    if not p.exists():
        return f"STATUS: missing\nPATH: {p}"
    if p.is_dir():
        entries = sorted(x.name + ("/" if x.is_dir() else "") for x in p.iterdir())
        return f"STATUS: dir\nPATH: {p}\n" + "\n".join(entries[:400])
    try:
        data = p.read_bytes()[:int(max_bytes)]
        text = data.decode("utf-8", errors="replace")
    except Exception as exc:
        return f"STATUS: error\nPATH: {p}\nREASON: {type(exc).__name__}: {exc}"
    size = p.stat().st_size
    note = f"\n...[truncated at {max_bytes} of {size} bytes]" if size > int(max_bytes) else ""
    return f"STATUS: ok\nPATH: {p}\nSIZE_BYTES: {size}\n--- CONTENT ---\n{text}{note}"


@mcp.tool()
def hermes_capabilities(query: str = "", category: str = "", limit: int = 60) -> str:
    """What Hermes can actually do — its procedural skill library.

    Hermes ships hundreds of skills: CAD/STEP modelling, LaTeX and Typst document
    production, Excel/Word/PDF work, statistics, ML pipelines (PyTorch, W&B), GitHub
    workflows, email, literature review, Obsidian notes, scientific plotting, LLM
    inference, and more. Prefer delegating to one of these over hand-rolling the
    steps yourself — knowing a skill exists changes the plan.

    Call with no arguments for a category summary, then narrow down.

    query:    case-insensitive substring match on skill name or description.
    category: restrict to a single category (e.g. "cad", "research", "productivity").
    Returns "name — description" lines, capped at `limit`.
    """
    skills = _skill_index()
    if not skills:
        return ("STATUS: empty\nREASON: no SKILL.md found. Checked:\n"
                + "\n".join(f"  - {r}" for r in _skill_roots()))

    q = query.strip().lower()
    cat = category.strip().lower()
    if cat:
        skills = [s for s in skills if s["category"].lower() == cat]
    if q:
        skills = [s for s in skills
                  if q in s["name"].lower() or q in s["description"].lower()]

    if not q and not cat:
        counts: dict[str, int] = {}
        for s in skills:
            counts[s["category"] or "(uncategorised)"] = counts.get(s["category"] or "(uncategorised)", 0) + 1
        lines = [f"STATUS: ok", f"TOTAL_SKILLS: {len(skills)}", "", "BY CATEGORY:"]
        for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"  {n:3d}  {name}")
        lines += ["", "Call hermes_capabilities(query=\"latex\") to search, or",
                  "hermes_capabilities(category=\"cad\") to list one category."]
        return "\n".join(lines)

    if not skills:
        return f"STATUS: no_match\nQUERY: {query!r}\nCATEGORY: {category!r}\nTOTAL_SKILLS: {len(_skill_index())}"

    lines = [f"STATUS: ok", f"MATCHES: {len(skills)} (showing {min(len(skills), int(limit))})", ""]
    for s in skills[:int(limit)]:
        tag = f"[{s['category']}] " if s["category"] else ""
        desc = s["description"][:220]
        lines.append(f"  {tag}{s['name']} — {desc}" if desc else f"  {tag}{s['name']}")
    if len(skills) > int(limit):
        lines.append(f"  ...{len(skills) - int(limit)} more")
    return "\n".join(lines)


@mcp.tool()
def hermes_health() -> str:
    """Check that the Hermes install is present. Instant -- never spawns the CLI.

    Reports the executable, the installed version (read from disk), the job
    counts, and the current blocking defaults. Use this to answer "is the bridge
    healthy" without waiting on a process start.
    """
    exe = Path(CFG["hermes_exe"])
    metas = _all_jobs()
    running = [m for m in _all_jobs() if str(m.get("state", "")).startswith("running")]
    return "\n".join([
        f"STATUS: {'ok' if exe.exists() else 'broken'}",
        f"EXE: {exe}",
        f"EXE_PRESENT: {exe.exists()}",
        f"HERMES_VERSION: {_installed_version() or 'unknown'}",
        f"CWD_DEFAULT: {CFG['default_cwd']}",
        f"JOB_TIMEOUT_DEFAULT_S: {CFG['default_timeout_s']}",
        f"BLOCKING_WAIT_DEFAULT_S: {CFG['default_wait_s']}",
        f"JOBS_ON_DISK: {len(metas)} ({len(running)} running)",
    ])


if __name__ == "__main__":
    _log(f"starting; hermes={CFG['hermes_exe']} (v{_installed_version() or '?'})")
    _log(f"allowed roots: {', '.join(CFG['allowed_roots'])}")
    mcp.run()
