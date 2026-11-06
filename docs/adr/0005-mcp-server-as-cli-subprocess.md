# MCP Server as a Thin Subprocess Adapter over the CLI

## Status

Accepted (recorded retrospectively on 2026-11-06).

## Context

The MCP server exposes the CLI's capabilities to agent hosts. Importing command functions directly would couple the server to Click internals, duplicate parameter validation and error shaping, and make cancellation of long-running workflows difficult. It would also create a second implementation whose behavior could drift from the CLI — the failure mode that made `tools/list` silently unusable in v1.18.0 (Issue #377).

The CLI already guarantees a stable machine contract: exactly one JSON envelope on stdout, logs on stderr, and registry-driven error metadata.

## Decision

The MCP server is a thin adapter:

- Every tool call is translated into `boss --json <argv>` by `mcp_args._build_args` and executed as a subprocess (`_run_boss` / `_run_boss_async`). The server parses the stdout envelope and never imports command functions.
- The tool catalog is derived from `schema/tool_catalog.py`, which builds on `schema/data.py`; `mcp-server/server.py` is a manually maintained re-export shim for the public surface.
- Handlers are registered with the mcp 2.x `server.add_request_handler(...)` API; registration is covered by tests because a missing handler makes a whole tool group unreachable without failing import.
- Long-running calls use the async subprocess path with bound timeouts; cancellation sends SIGINT so wizard/automation can reach their normal checkpoint path, and never signals the user's attached browser process. Unknown outcomes are reported with `outcome_unknown=true`, not auto-retried.
- Tests assert that `tools/list` matches `TOOLS` exactly, that every declared enum value and required argument produces argv the Click tree accepts, and that parameter name drift (for example `--tag` vs `--label`) fails CI.

## Consequences

- MCP behavior cannot drift from the CLI without the CLI contract drifting first; there is one implementation of each capability.
- Adding a CLI option does not automatically expose it over MCP: the tool schema and `_build_args` mapping must be updated, and the compliance-command override map must be updated when tool and command names differ.
- The server inherits the CLI's envelope, error codes and recovery metadata, so agent hosts see the same `recoverable` / `recovery_action` semantics on both surfaces.
- Process isolation bounds the blast radius of crashes and makes timeouts/cancellation implementable, at the cost of process start-up latency and a manual re-export list to maintain.
