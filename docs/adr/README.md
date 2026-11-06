# Architecture Decision Records

This directory records the decisions that shape boss-agent-cli's public contracts. ADRs are written in English so the rationale stays in one place; user-facing documentation remains bilingual.

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-default-low-risk-assistance-mode.md) | Default to Low-Risk Assistance Mode | Superseded (historical context) |
| [0002](0002-explicit-research-operating-mode.md) | Add an Explicit Research Operating Mode | Superseded (historical context) |
| [0003](0003-browser-channel-policy-table.md) | Browser Channel Selection via a Frozen Policy Table | Accepted |
| [0004](0004-envelope-and-error-contract.md) | The Seven-Key Envelope and Registry-Driven Error Contract | Accepted |
| [0005](0005-mcp-server-as-cli-subprocess.md) | MCP Server as a Thin Subprocess Adapter over the CLI | Accepted |

## Adding a record

- Copy the structure of ADR 0003: `## Status`, `## Context`, `## Decision`, `## Consequences`.
- Number files as `NNNN-short-title.md`, one decision per record.
- A record that no longer reflects the code is marked **Superseded** and kept for context — do not rewrite history.
- Update this index in the same change.
