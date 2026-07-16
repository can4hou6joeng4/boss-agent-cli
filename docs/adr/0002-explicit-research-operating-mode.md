# Add an Explicit Research Operating Mode

## Status

Accepted.

## Context

ADR 0001 made low-risk local assistance the only supported operating posture. That protected the default Agent surface, but it also prevented maintainers from reviewing and shipping bounded browser-protocol research, anti-debugging hooks, risk-control adaptation, and resumable collection infrastructure.

## Decision

The project supports two operating modes:

- `assisted` is the default. It preserves the existing low-risk behavior and blocks sensitive commands with `COMPLIANCE_BLOCKED`.
- `research` is explicitly enabled by the user. It may expose browser-protocol, anti-debugging, risk-control adaptation, controlled collection, and sensitive workflow capabilities declared by the shared capability-policy registry.

Research Mode does not authorize silent activation, unbounded runs, uncontrolled retries, credential logging, or indefinite data retention. Research adapters must use explicit activation, bounded work, checkpoint/stop behavior, independent browser profiles where applicable, redacted diagnostics, and auditable third-party script provenance.

`boss schema` is the operating-mode and capability-policy source of truth. CLI and MCP surfaces must derive restrictions from the same registry.

## Compatibility

The default remains `assisted`, so existing installations keep their current behavior. Historical internal configuration with `low_risk_mode=false` maps to `research`; new configuration uses only `operating_mode`.

## Consequences

- High-risk research may be reviewed and released without silently changing the default product posture.
- Every gated capability needs risk and data classification.
- Browser hook and collection changes require stronger provenance, redaction, bounded-run, and CI evidence.
- MCP remains assisted-only until a dedicated mode-aware exposure contract is implemented.
