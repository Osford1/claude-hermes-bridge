# Credits

Nothing third-party is vendored in this repository. What follows is what the
bridge is built on and what it learned from, with the exact evidence.

## Specification and protocol

- **Model Context Protocol (MCP)** — the open standard this server speaks.
  Anthropic, <https://modelcontextprotocol.io/>. The server targets the stdio
  transport and the tool-calling surface; it uses protocol revision
  `2025-11-25` (that is the version Claude Desktop negotiated, per its own log).
- **MCP Python SDK** (`mcp`) — the client/server library the server imports.
  <https://github.com/modelcontextprotocol/python-sdk>. The server uses the
  `FastMCP` convenience class (`mcp.server.fastmcp`), which lives in SDK 1.x and
  not in 2.0.0; see `requirements.txt`.

## The agent it drives

- **Hermes Agent** by **Nous Research** — the general agent (shell, file and
  browser tools, plus a large procedural-skill library) that the bridge delegates
  to via the `hermes` CLI. The machine this was built against runs Hermes
  `0.21.0` (read from disk on 2026-11-06). <https://hermes-agent.nousresearch.com/>

## Skills that shaped this repository

Both are Hermes skills authored on the same machine that runs the bridge; they
are what the bridge is for, documented from the inside.

- **`claude-code`** — the Hermes skill for delegating coding work to the Claude
  Code CLI. It established the pattern this project inverts: a session that plans,
  and a session that executes, kept separate. The bridge is the same idea with
  the direction of control reversed — Claude Desktop plans, Hermes executes.
- **`claude-hermes-bridge`** — the Hermes skill defining how this bridge is used
  from the Hermes side (registering the server, the job model, the limits of an
  unattended executor). Its operating rules are the source of the "state absolute
  paths" and "a tool reporting success is not evidence" guidance repeated in the
  server's own `INSTRUCTIONS` string and in the README.

## Prior art

No prior art was copied. The design is small and the interesting constraints
(Claude Desktop's tool timeout, the disk-backed job model) were discovered by
running it, not by reading another bridge. If you know of a project that solved
the same stdout/timeout problem first, open an issue and it will be credited here.
