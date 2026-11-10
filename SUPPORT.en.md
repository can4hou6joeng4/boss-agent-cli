# Getting Support

> When a command fails, read [Troubleshooting](docs/troubleshooting.en.md) first — the error envelope's `error.code`, `error.recoverable`, and `error.recovery_action` usually already name the recovery step. 中文版见 [SUPPORT.md](SUPPORT.md).

## Read these first

- [Getting started](docs/getting-started.en.md) — install, log in, first command
- [Troubleshooting](docs/troubleshooting.en.md) — common errors, CDP / patchright, risk-control locks
- [Command reference](docs/commands.en.md) — every command and option
- [Agent integration](docs/agent-quickstart.en.md) — MCP / JSON envelope / Python API
- Local checks: `boss doctor`, `boss status`, `boss schema --format native`

## Where to ask

| Kind of question | Channel | Notes |
|------------------|---------|-------|
| Usage questions, install problems, agent integration | [GitHub Discussions](https://github.com/can4hou6joeng4/boss-agent-cli/discussions) | Open-ended discussion; it does not have to be a confirmed bug |
| Reproducible bugs / feature requests | [Issues](https://github.com/can4hou6joeng4/boss-agent-cli/issues/new/choose) | Use the templates; blank issues are disabled |
| Security vulnerabilities | [SECURITY.md](SECURITY.md) | Report privately; do not open a public issue |

## What to include

- The output of `boss --version`
- The full command, including global options such as `--platform` and `--browser-source`
- The error envelope's `error.code`, `error.message`, and `error.recovery_action`
- Your OS and install method (`pip` / `uv tool` / source checkout)

**Redact first**: never paste cookies, tokens, phone numbers, WeChat IDs, real names, or real `security_id` values.

## Response expectations

- This project is maintained in spare time on a best-effort basis; no response time is promised.
- Issues and PRs inactive for 60 days are marked `stale` and closed 14 days later; a reply or a new commit keeps them open.
- Security reports follow [SECURITY.md](SECURITY.md); details are not discussed in public channels.
