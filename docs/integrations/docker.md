# Docker Integration

Applies to the `boss-agent-cli` low-risk CLI contract as of version 1.17.0.

The repository ships a `Dockerfile` and a `docker-compose.yml` so you can run the MCP
server without setting up a local Python toolchain. This is the answer to
"environment setup is painful, especially on Apple Silicon" — with one important
boundary spelled out below.

## What the image is and is not for

**It runs**: the MCP server (`boss-mcp`), plus read-only and local commands
(`status`, `doctor`, `search`, `detail`, `shortlist`, `stats`, `schema`, …).

**It does not run `boss login`.** Login needs a real browser — QR scanning or
extracting cookies from your own browser profile — so the image deliberately ships
**without a browser kernel**. Bundling one would produce an image that looks like it
works and then cannot log in. Log in on the host, then mount the credentials.

The same applies to any command that needs the browser channel: `resume export pdf`,
`crawl run`, and CDP-backed automation paths stay on the host.

No image is published to a registry. Building locally from this repo keeps the
"one command to start" experience without a release-time image rebuild and
registry-drift problem.

## Prerequisites

Log in once on the host:

```bash
uv tool install boss-agent-cli
boss login
boss status          # should report a live session
```

Credentials land in `~/.boss-agent/` (Fernet-encrypted, machine-bound key).

> The encryption key is derived per machine. Credentials are portable to a container
> on the **same** host, but copying `~/.boss-agent` to a different machine will not
> decrypt.

## Build

```bash
docker build -t boss-agent-cli:local .
```

Dependencies are installed with `uv sync --frozen`, so the image resolves exactly what
`uv.lock` pins — a build fails rather than silently drifting. The final image is about
495 MB, dominated by the `patchright` wheel that ships as a core dependency.

## Run

### Via docker compose (recommended)

```bash
BOSS_UID=$(id -u) BOSS_GID=$(id -g) docker compose run --rm boss-mcp
```

Override the data directory when you want project-level isolation:

```bash
BOSS_UID=$(id -u) BOSS_GID=$(id -g) \
BOSS_DATA_DIR=~/projects/foo/.boss-agent \
docker compose run --rm boss-mcp
```

### Via docker run

```bash
docker run --rm -i \
  --user "$(id -u):$(id -g)" \
  -v ~/.boss-agent:/data/.boss-agent \
  boss-agent-cli:local
```

### A one-off CLI command

`ENTRYPOINT` is `boss-mcp`, so override it to reach the CLI directly:

```bash
docker run --rm \
  --user "$(id -u):$(id -g)" \
  -v ~/.boss-agent:/data/.boss-agent \
  --entrypoint boss boss-agent-cli:local \
  --json search "golang" --city 101010100
```

### HTTP or SSE transport

```bash
docker run --rm -p 8765:8765 \
  --user "$(id -u):$(id -g)" \
  -v ~/.boss-agent:/data/.boss-agent \
  boss-agent-cli:local \
  --transport http --host 0.0.0.0 --port 8765
```

## Always pass `--user`

The image runs as non-root `boss` (UID 1000). If your host UID differs — macOS
typically starts at 501 — a container writing into the mounted directory would create
files your host user does not own. Passing `--user "$(id -u):$(id -g)"` keeps
ownership straight in both directions.

Inside the container `HOME=/data`, so the CLI's default `~/.boss-agent` resolves to
`/data/.boss-agent` for `boss-mcp` and for `--entrypoint boss` alike. `/data` is mode
`0755` specifically so an overridden UID can still traverse it.

## Wiring it into an MCP host

Point the host at `docker run` instead of `uvx`:

```json
{
  "mcpServers": {
    "boss-agent-cli": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "--user", "1000:1000",
        "-v", "/absolute/path/to/.boss-agent:/data/.boss-agent",
        "boss-agent-cli:local"
      ]
    }
  }
}
```

MCP hosts generally do not expand `$(id -u)` or `~`, so use literal values and
absolute paths there.

## Compliance boundary is unchanged

The container inherits the same defaults as the CLI: `assisted` mode blocks automated
outreach, bulk actions, and candidate personal-data workflows, and MCP exposes only the
low-risk tool surface. Running in a container does not widen what the tool will do —
see [platform-risk.md](../platform-risk.md).

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `PermissionError: '/data/.boss-agent'` | host UID cannot traverse or write the mount | pass `--user "$(id -u):$(id -g)"` |
| `AUTH_REQUIRED` envelope | no credentials in the mounted directory | run `boss login` on the host first, and check the mount path |
| `BROWSER_KERNEL_MISSING` | a browser-dependent command was invoked | run that command on the host; the image ships no browser by design |
| MCP host reports no tools | `stdin` was not kept open | `docker run` needs `-i`; compose needs `stdin_open: true` |

## Verified behavior

The image is built in CI on every push and pull request (`docker` job in
`.github/workflows/ci.yml`) so it cannot rot silently the way an unreferenced
Dockerfile does. The build was validated end to end: MCP `initialize`, `tools/list`
returning the full low-risk tool set, and a `tools/call` round trip that shells out to
`boss` inside the container.
