#!/usr/bin/env python
"""Register claude-hermes-bridge with Claude Desktop.

Writes (or updates) an ``mcpServers.hermes`` entry in Claude Desktop's
``claude_desktop_config.json``, pointing at this repo's ``server.py`` and at the
Python interpreter running this script. The existing config is backed up first.

Run it with the interpreter that will run the server, so the two match:

    python register_with_claude.py            # write the entry
    python register_with_claude.py --print     # show what it would write, no change

Restart Claude Desktop after a change. Nothing here touches the network.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE / "server.py"
PYTHON = Path(sys.executable)

SERVER_NAME = "hermes"

# Claude Desktop looks in more than one place depending on how it was installed:
#   store/msix  -> %LOCALAPPDATA%\Claude-Data\<pkg>\claude_desktop_config.json
#   classic     -> %APPDATA%\Claude\claude_desktop_config.json
# We write every config we can find so both installs pick up the same server.
def candidate_configs() -> list[Path]:
    out: list[Path] = []
    appdata = os.environ.get("APPDATA")
    local = os.environ.get("LOCALAPPDATA")
    if appdata:
        out.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
    if local:
        # The Store build drops its config under a package-named subdirectory.
        data = Path(local) / "Claude-Data"
        if data.is_dir():
            out.extend(sorted(data.glob("*/claude_desktop_config.json")))
    return out


def entry() -> dict:
    return {"command": str(PYTHON), "args": [str(SERVER)]}


def merge(path: Path, block: dict, dry_run: bool) -> bool:
    if path.exists():
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  ! {path} is not valid JSON ({exc}); refusing to touch it")
            return False
    else:
        cfg = {}

    current = (cfg.get("mcpServers") or {}).get(SERVER_NAME)
    if current == block:
        print(f"  = {path} already has the correct entry")
        return False

    if dry_run:
        print(f"  would write to {path}:")
        print("    " + json.dumps({"mcpServers": {SERVER_NAME: block}}, indent=2).replace("\n", "\n    "))
        return True

    cfg.setdefault("mcpServers", {})[SERVER_NAME] = block
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, path.with_suffix(path.suffix + f".bak-{stamp}"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print(f"  + wrote {path}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", dest="dry_run", action="store_true",
                    help="show the change without writing it")
    args = ap.parse_args()

    if not SERVER.exists():
        print(f"server.py not found next to this script ({SERVER})", file=sys.stderr)
        return 2

    block = entry()
    print(f"server : {SERVER}")
    print(f"python : {PYTHON}")
    print(f"entry  : {json.dumps({'mcpServers': {SERVER_NAME: block}})}")

    targets = candidate_configs()
    if not targets:
        print("\nNo Claude Desktop config directory found. Create "
              r"%APPDATA%\Claude\claude_desktop_config.json" " and add the block "
              "manually — see the README.")
        return 1

    print("\nconfig files:")
    changed = False
    for path in targets:
        changed |= merge(path, block, args.dry_run)

    print("\nRestart Claude Desktop to pick up the change." if changed
          else "\nNothing to do.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
