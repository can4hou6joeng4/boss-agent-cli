# The Seven-Key Envelope and Registry-Driven Error Contract

## Status

Accepted (recorded retrospectively on 2026-11-06).

## Context

boss-agent-cli has four entry points — the terminal wizard, the JSON envelope, the MCP server and the Python API. Agents need a single machine-readable shape that does not change per command, and they need to decide whether a failure is retryable without parsing prose. Early versions leaked raw Click text, English OS errors and generic `NETWORK_ERROR` retries for deterministic failures, which caused agents to retry the same invalid input or the same local I/O problem indefinitely.

## Decision

Every command writes exactly one JSON object to stdout with exactly seven keys:

```json
{"ok": ..., "schema_version": "1.0", "command": ..., "data": ..., "pagination": ..., "error": ..., "hints": ...}
```

- `exit 0` ⇔ `ok=true`; `exit 1` ⇔ `ok=false`. `--exit-codes sysexits` maps error classes to semantic exit codes without changing the envelope.
- Every error envelope carries `code`, `recoverable` and `recovery_action`. Metadata lives in `schema/error_codes.py`, is published through `boss schema`, and is the single source used by display/output helpers.
- Usage errors raised by Click — including localized custom validators — are translated into `INVALID_PARAM` envelopes; they never escape as raw English text or as retryable network errors.
- Deterministic failures (`TypeError`, `AttributeError`, `KeyError`, `IndexError`, `OverflowError`) map to `INTERNAL_ERROR` (`recoverable=false`); local filesystem failures map to `LOCAL_IO_ERROR`; platform risk maps to terminal stop codes. Only genuine transport problems remain retryable.
- `hints` has two audiences: `next_actions` (commands an agent can execute, shell-quoted where they embed user input) and `operator_actions` (natural-language instructions for a human). A TTY renders only the latter, to stderr.
- Logging and terminal rendering never write to stdout. The output layer redacts sensitive keys before serializing.

## Consequences

- All four entry points share one contract; the MCP server parses the same stdout envelope.
- New error codes must be registered with message, `recoverable` and non-empty recovery text; tests enforce this and reject unregistered codes.
- Every command is testable against the same invariants (single stdout object, exit-code mapping, error keys, redaction).
- Changing the envelope shape or error semantics is a breaking change and requires a release note and ADR update.
