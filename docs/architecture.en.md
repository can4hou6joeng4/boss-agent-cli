# Architecture

This document is for contributors reading the source for the first time, and for agent hosts that need to understand the contract. It explains the layers, how a call flows, and which constraints must not be broken. Command and parameter details live in the [Command Reference](commands.en.md); the capability source of truth is always `boss schema`.

## Design Goals

- **One capability set, four entry points**: the terminal wizard, the JSON envelope (pipes / scripts), the MCP server and the Python API all use the same commands and the same local state.
- **Local-first**: apart from platform requests the user explicitly triggers, data does not leave the machine; there is no runtime telemetry, analytics or usage tracking.
- **Orchestratable**: stdout carries exactly one JSON envelope; logs and terminal rendering go to stderr.
- **Bounded execution**: every long workflow is constrained by timeout, retry, request budget, checkpoint and stop controls; platform risk is a hard boundary.

## Four Entry Points

| Entry point | Shape | Typical use |
| --- | --- | --- |
| `boss` (TTY) | Terminal wizard that asks step by step and renders results | First-time human use, multi-step tasks |
| `boss <command> --json` / pipes | One seven-key JSON envelope per command | Shell, scripts, non-interactive agents |
| `boss-mcp` | MCP tools over stdio / SSE / HTTP | Claude Desktop, Cursor, Windsurf, Codex and similar hosts |
| `from boss_agent_cli import AuthManager, BossClient` | Python embedding API | Custom scripts and services |

All four share the same command implementations, the same `~/.boss-agent/` local state and the same error codes.

## Envelope Contract (Do Not Break)

Every command writes exactly one JSON object to **stdout**, with exactly seven keys:

```json
{"ok": true, "schema_version": "1.0", "command": "search", "data": [], "pagination": null, "error": null, "hints": {"next_actions": []}}
```

- `exit 0` ⇔ `ok=true`, `exit 1` ⇔ `ok=false`; `--exit-codes sysexits` returns semantic exit codes per error class.
- Error envelopes must carry `error.code` / `error.recoverable` / `error.recovery_action`; codes are registered in `schema/error_codes.py`.
- `hints.next_actions` holds commands for the agent to run; `hints.operator_actions` holds natural-language guidance for the human operator. TTY renders only the latter, to stderr.
- The output layer redacts sensitive keys in `data` / `hints`.

## Capability Source of Truth: `boss schema`

`schema/data.py` describes commands, parameters, availability, global options and error codes. `boss schema` renders it as native JSON, or converts it to `openai-tools` / `anthropic-tools` / `mcp-tools`. The MCP tool catalog is derived from the same data plus `schema/tool_catalog.py`, so **schema and the real commands must stay in sync** — tests check both directions.

## Layers

```text
main.py                 Click group, global options, envelope shaping for usage errors
  └─ commands/register.py   registers the 39 top-level commands and the hr group
       └─ commands/<cmd>.py  Click command; validates parameters, calls platform/service, emits the envelope
            ├─ commands/_platform.py / _recruiter_platform.py   resolve a platform instance from ctx
            │    └─ platforms/factory.py → Platform / RecruiterPlatform registries
            ├─ services/    reusable logic (exports, snapshots, contact lookup, job_card mapping)
            └─ api/         transport layer
                 ├─ client.py / recruiter_client.py   httpx clients, throttling, error mapping
                 ├─ browser_client.py                CDP / headless browser channel
                 ├─ browser_source.py                channel policy table (the single dispatch point)
                 └─ *.yaml                           source of truth for endpoints / headers / platform codes
       └─ auth/AuthManager      login state: cookie extraction → CDP probe → patchright QR; Fernet encryption
       └─ wizard/               recoverable workflow runner shared by the TTY wizard and --input-json
       └─ automation/           recruiter browser / CDP automation (safety caps, budget, checkpoint)
       └─ crawler/              resumable crawling (SQLite checkpoints, isolated chrome-profile)
  └─ output.py / display.py  JSON envelope + stderr rendering + error contract
  └─ mcp_tools.py / mcp_server.py   MCP tool catalog, transports, subprocess calls to the boss CLI
  └─ cache/store.py          SQLite (WAL) local cache
  └─ schema/                 capability data, error codes, tool catalog and format conversion
```

Design constraints:

- **Command modules never construct clients directly**; they go through `_platform` / `_recruiter_platform`, and the platform is chosen by `--platform` or config.
- **`commands/` holds only two kinds of modules**: Click command definitions, and `_`-prefixed CLI support modules. Reusable logic that does not depend on Click / display lives in `services/`.
- **wizard does not import command modules** and does not call `_`-prefixed functions; it reuses capabilities through services and the runner.
- **The MCP server calls the `boss` CLI as a subprocess** and parses the stdout envelope instead of importing command functions — the envelope contract is therefore also the MCP contract.

## Call Flow for `boss search`

1. `main.py` parses global options, validates `--data-dir` / `--cdp-url` / `--delay`, loads `config.json` and builds `ctx.obj`.
2. `commands/register.py` resolves the `search` command; the command validates the query and filters, expanding `--preset` / `--url` when needed.
3. `_platform.get_platform_instance(ctx, auth)` constructs `BossPlatform` and its client from the platform registry.
4. The client selects a channel through the `--browser-source` policy table (CDP → headless, or httpx-only), sends the request under throttling and request budget, and parses the response shape declared in the endpoint YAML.
5. The command maps results into `data` and `pagination`; `display.handle_output` renders them on a TTY or emits the envelope on a pipe.
6. If any layer raises `PlatformRiskError` (account / environment risk), the current workflow stops immediately, saves a checkpoint, and `display` returns the shared recovery action.

## Login and Authentication

`auth/AuthManager` keeps encrypted login state (Fernet with a machine-bound key). Login tries, in the order declared by the platform: browser cookie extraction → CDP (a read-only probe before reusing an existing logged-in context; on failure it falls back to a forced login) → patchright QR. Login state stays local; the CLI does not read or upload unrelated browser data.

## Platform Abstraction

`Platform` (candidate side) and `RecruiterPlatform` (recruiter side) are two independent registries. The only registered platform today is BOSS Zhipin (`zhipin` / `zhipin-recruiter`). The multi-platform abstraction is kept, but adding a platform requires endpoint tables, capability availability, schema and tests — see [Platform Abstraction](platform-abstraction.en.md).

## Long Workflows, Budget and Risk

- Batch crawling, recruiter automation and wizard workflows are all constrained by five controls: timeout, retry, request / detail / wall-clock budget, checkpoint and stop.
- On account risk, environment risk or a security-verification page, the current workflow **stops immediately and saves a checkpoint**. There is no unbounded retry and no bypass through CDP, patchright or cookie extraction; resuming is explicit and user-driven.
- The source of truth for risk error codes and recovery text is `schema/error_codes.py` and `display.RISK_ERROR_CONTRACTS`.

## Local Data Directory

Default `~/.boss-agent/` (override with `--data-dir`):

```text
config.json          user configuration
auth/session.enc     encrypted login state (Fernet, machine-bound)
cache/boss_agent.db  SQLite cache: search history, shortlist, presets, watch, crawl checkpoints
chat-export/         chat snapshots and exports
resumes/             local resumes
models/              local model registry (weights stay external)
crawl/               isolated Chrome profile and crawl artifacts
automation/          recruiter automation state and deduplication records
```

These files are used on this machine only; back them up as needed before uninstalling or resetting.

## Extending the Capability Surface

When adding a command / option / MCP tool, follow the checklists in [CONTRIBUTING](../CONTRIBUTING.en.md) ("Adding a New Command" and "Output Contract") plus the schema, document-count and version-sync items in the [Release Checklist](maintainer/release-checklist.md). Key architectural decisions are recorded in the [ADR index](adr/README.md).
