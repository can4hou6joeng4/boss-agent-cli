# Browser Channel Selection via a Frozen Policy Table

## Status

Accepted (October 2026; recorded retrospectively on 2026-11-06).

## Context

The CLI can reach the platform through several channels: an existing CDP browser, a headless Chromium it launches itself, or plain httpx. It also exposes a `--browser-source` option (`auto`, `existing-browser`, `stored-cookie`, `httpx-only`) that describes where the login state comes from, not a transport name.

Before the policy table, channel ordering and credential rules were spread across conditionals in the browser client. That made two failure modes easy:

- a source that was supposed to be fail-closed silently fell back to a broader channel;
- adding a channel required remembering every mode switch, and one missed branch produced a fail-open path.

Issue #387 also established that copying stored cookies into another browser profile is not an acceptable fallback (credential exposure and session races).

## Decision

Channel selection is data, not control flow:

- `api/browser_source.py` defines a frozen `BrowserSourcePolicy` per source with an ordered `channels` whitelist and explicit capabilities: whether stored credentials may be used, whether a new browser context may be created, whether the process may launch a browser, whether localhost CDP auto-probing is allowed, and which failure code/message/hints apply when the whitelist is exhausted.
- `_ensure_started` dispatches only through `policy.channels`; the order is defined in the table and nowhere else.
- Only `auto` is fail-open (`failure_code is None`). Every explicit source is fail-closed and returns its registered error code instead of upgrading to a broader channel.
- The CLI, `config set browser_source`, `boss schema` and the MCP surface derive their value domain from the same `POLICIES` table.
- Failure codes remain registered in `schema/error_codes.py`; the policy table only chooses which one applies.

## Consequences

- Adding a channel means adding a dispatch branch plus a test gate (`test_every_declared_channel_is_dispatchable`); adding a source means adding one table row with complete metadata.
- Explicit sources can no longer silently widen access; failures carry stable codes (`BROWSER_SESSION_NOT_FOUND`, `CDP_UNAVAILABLE`, `BROWSER_DISABLED`, …) and operator guidance.
- Schema, CLI choices and MCP choices cannot drift from the table without a failing test.
- The policy table is part of the public contract: changing a row is a behavior change and must be covered by tests and release notes.
