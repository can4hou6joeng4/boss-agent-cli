# Documentation Index

Every public document for boss-agent-cli lives here. Start with "Getting started" on your first visit; read "Core reference" and "Agent integration" before wiring up an agent or changing code. `boss schema` remains the capability source of truth.

## Getting started

- [Getting started](getting-started.en.md) — install, log in, first command
- [Troubleshooting](troubleshooting.en.md) — common errors, CDP / patchright, error codes and recovery actions

## Core reference

- [Command reference](commands.en.md) — every command, option, and the JSON envelope
- [Capability matrix](capability-matrix.en.md) — capability parity across CLI / JSON / MCP / Python API
- [Architecture](architecture.en.md) — module layering, platform abstraction, workflows and state
- [Platform abstraction](platform-abstraction.en.md) — the Platform / RecruiterPlatform registries

## Agent integration

- [Agent quickstart](agent-quickstart.en.md) — envelope contract, error recovery, risk-stop rules
- [Agent host examples](agent-hosts.en.md) — Claude Code / Cursor / Codex and others

## Boundaries & security

- [Platform risk boundaries](platform-risk.en.md) — cookies / CDP / request rates / hard risk-control limits

## Integrations

- [AI models](integrations/ai-models.en.md) — provider capability boundaries
- [Claude Code](integrations/claude-code.md)
- [Cursor](integrations/cursor.md)
- [Codex](integrations/codex.md)
- [Windsurf](integrations/windsurf.md)
- [Docker](integrations/docker.md)
- [Python SDK](integrations/python-sdk.md)
- [Shell / agent orchestration](integrations/shell-agent.md)

## Architecture decisions (ADRs)

- [ADR index](adr/README.md) — recorded architecture decisions

## Maintainers

- [Release checklist](maintainer/release-checklist.md)
- [Branch protection](maintainer/branch-protection.md)
- [Label conventions](maintainer/labels.md)
- [README badge policy](maintainer/readme-badge-policy.md)
- [Smoke testing](maintainer/smoke-testing.md)

## Research notes

- [Platform research index](research/platforms/README.md) — early API research for Lagou / Liepin / BOSS Zhipin
- [Upstream anti-detection evaluation (2026-10-29)](research/upstream-antidetection-evaluation-2026-10-29.md)
- [BOSS Zhipin CDP read-only path (2026-10-29)](research/zhipin-cdp-free-read-path-2026-10-29.md)
- [BOSS Zhipin request identity (2026-10-29)](research/zhipin-request-identity-2026-10-29.md)
