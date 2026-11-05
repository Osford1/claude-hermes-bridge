# Adding a project to the bridge

The bridge will only run work inside directories you have allow-listed. This is
deliberate: an MCP tool is an **unattended shell** — Claude Desktop calls it with
no human in the loop at call time — so the safe default is "nothing outside these
roots", not "anything on the disk".

This page is the short version of how that confinement works and how to widen it.

## Where the list lives

`server.py` reads `config.json` from its own directory at startup, falling back to
the `DEFAULTS` dict in the file if no config is present. The relevant keys:

| Key | Meaning |
| --- | --- |
| `allowed_roots` | The list of directories work is confined to. A `cwd` must resolve *inside* one of these or the call is refused with `STATUS: refused`. |
| `default_cwd` | Used when a tool call omits `cwd`. Must itself sit inside an allowed root. |
| `hermes_exe` | Absolute path to the `hermes` executable the server spawns. |
| `default_max_turns` | Turn cap per task if the caller does not set `max_turns`. |
| `default_timeout_s` | Wall-clock cap the server enforces itself, independent of the client. |
| `default_wait_s` | How long a blocking call holds the client before returning a job id. Keep it well under Claude Desktop's 180 s tool timeout. |
| `max_output_chars` | Truncation ceiling for the output echoed back to Claude. |

Start from `config.example.json`, copy it to `config.json` (which `.gitignore`
keeps out of version control), and edit the paths.

## How confinement is enforced

`_resolve_cwd()` in `server.py`:

1. Takes the requested `cwd`, or `default_cwd` if none was given.
2. Resolves it to an absolute path.
3. Checks whether that resolved path is inside any entry of `allowed_roots`.

If it is not, the tool returns:

```
STATUS: refused
REASON: cwd <path> is outside the allow-list:
  - <root 1>
  - <root 2>
```

and **no subprocess is started**. The refusal happens before anything runs.

Two things worth stating plainly, because the mechanism is easy to over-trust:

- The check is on the **working directory**, not on every path the agent then
  touches. Once Hermes is running inside an allowed root it has the toolsets
  Hermes itself has; this list scopes *where the job starts*, it does not sandbox
  what a running agent can reach.
- It is a **containment boundary for accidents**, not a security boundary against
  an adversary. See the Security section of the README before you widen it.

## Adding a root

1. Open `config.json`.
2. Add the absolute path to `allowed_roots`:

   ```json
   "allowed_roots": [
     "C:\\Users\\<you>\\Projects",
     "C:\\Users\\<you>\\claude-work",
     "D:\\work\\my-new-project"
   ]
   ```

   Use absolute paths. Keep the `\\` doubling that JSON requires on Windows.

3. If the new root should also be the default place work lands, point
   `default_cwd` at it (or a subdirectory of it).
4. Restart the server. Claude Desktop spawns a fresh server process per session,
   so the new list is read the next time Claude Desktop starts it; there is no
   live reload.

## Adding a whole project rather than a folder

The bridge has no concept of a "project" beyond a directory. If you want Claude
Desktop to work on a specific repository, allow-list that repository's parent
directory (or the repository itself, if the work never needs to write outside it),
and pass an absolute `cwd` in the `hermes_run` call:

```
hermes_run(
  task="Run the test suite and report the failing test names verbatim.",
  cwd="D:\\work\\my-new-project"
)
```

The agent's own operating conventions — writing an explicit definition of "done",
stating absolute paths — are the caller's responsibility. See the bridge's
`INSTRUCTIONS` string in `server.py` for what Claude Desktop is told about that.
