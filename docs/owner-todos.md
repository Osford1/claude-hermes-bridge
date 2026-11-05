# Owner TODOs

Open items before this repo is fully "done". Kept here rather than in the README
so the README stays a product document.

| # | Item | Why it matters |
| --- | --- | --- |
| 1 | Take one screenshot of Claude Desktop showing the `hermes` server connected (Settings → Developer → MCP), ~1200 px wide. | The README claims a Claude Desktop connection. A real image is the checkable proof. Do not fabricate one — take it or leave the claim as text-only. |
| 2 | Confirm the `LICENSE` copyright line: `Copyright (c) 2026 Chen Bin`. | Legal text; needs the owner's exact preferred name form. |
| 3 | Decide whether to keep the dated `Hermes v0.21.0` line in the README. | It is a real reading from 2026-11-06 and will age. Keep it dated, or drop the number. |
| 4 | Decide whether `server.py` should be reformatted further, or left byte-similar to the running copy. | Only the dead duplicated `JOBS/_JOBS_LOCK` block was removed; the rest matches what actually runs. |
| 5 | If a non-Windows build is wanted, say so. | `_pid_alive()` uses `ctypes.windll`; `run_server.cmd` is a batch file. The README marks the project Windows-first. Porting is real work, not a README edit. |
| 6 | Run `tests/jobtest.py` and `tests/e2e_newmodel.py` once on a warm machine and paste the output into the README's Testing section. | Those two need a live Hermes CLI; only the tool-listing smoke test was re-run for the initial README. |
