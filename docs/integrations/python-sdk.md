# Python Direct Integration (OpenAI / Claude SDK)

Use this path when you do not want MCP in the loop. Your own Python agent can call the OpenAI Functions API or Claude Tool Use API directly, while `boss-agent-cli` remains the execution backend.

## Good fit when

- you run a custom agent framework (LangGraph, LlamaIndex, or a hand-written loop)
- you want BOSS job-hunt capability embedded in existing business code
- you want to test new models against the exported `boss` tool surface
- you run scheduled follow-up tasks in CI/CD

## Core idea

`boss schema` supports three output formats:

| `--format` | Output shape | SDK target |
|-----------|---------|---------|
| `native` (default) | project-specific JSON envelope | manual parsing / MCP conversion |
| `openai-tools` | OpenAI Functions / Tools API compatible | `openai` Python SDK |
| `anthropic-tools` | Claude Tool Use API compatible | `anthropic` Python SDK |

The latter two can be passed directly into the relevant SDK's `tools=` parameter with no hand-written conversion layer.

Notes:
- every command in the `native` schema includes `availability`, which lets your application pre-filter by `role` or `platform`
- the exported `description` fields in `openai-tools` and `anthropic-tools` also carry the same availability hints so the model can see the boundary conditions directly

## Minimal OpenAI example

`examples/openai_agent.py`:

```python
"""Drive boss-agent-cli with OpenAI GPT-4o.

Run:
    pip install openai
    export OPENAI_API_KEY=sk-...
    boss login  # make sure auth is ready first
    python examples/openai_agent.py "Find Python backend jobs in Shanghai above 30K"
"""
import json
import subprocess
import sys
from openai import OpenAI

def run_boss(*args):
    """Call the boss CLI and return the parsed JSON envelope."""
    result = subprocess.run(
        ["boss", "--json", *args],
        capture_output=True, text=True, timeout=120,
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": {"code": "CLI_ERROR", "message": result.stderr}}


def load_tools():
    """Load boss schema in openai-tools format and feed it directly to the SDK."""
    out = subprocess.run(
        ["boss", "schema", "--format", "openai-tools"],
        capture_output=True, text=True,
    ).stdout
    return json.loads(out)["data"]["tools"]


def main(user_prompt: str):
    client = OpenAI()
    tools = load_tools()
    messages = [
        {"role": "system", "content": "You are a job-hunt assistant. Use boss_* tools to operate BOSS Zhipin. Every tool returns a JSON envelope; if ok=false, explain the failure to the user."},
        {"role": "user", "content": user_prompt},
    ]

    for _ in range(10):  # at most 10 tool rounds
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            tools=tools,
        )
        msg = resp.choices[0].message
        messages.append(msg)

        if not msg.tool_calls:
            print(msg.content)
            return

        for call in msg.tool_calls:
            # Tool name looks like "boss_search"; convert back to CLI command name.
            cmd = call.function.name.replace("boss_", "").replace("_", "-")
            args = json.loads(call.function.arguments)

            # Flatten dict arguments into CLI arguments.
            cli_args = [cmd]
            for key, value in args.items():
                if key == "query" or (len(args) == 1 and isinstance(value, str)):
                    cli_args.append(str(value))
                else:
                    cli_args.extend([f"--{key.replace('_', '-')}", str(value)])

            output = run_boss(*cli_args)
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(output, ensure_ascii=False),
            })


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "Search Python developer jobs in Beijing")
```

Run:

```bash
export OPENAI_API_KEY=sk-...
boss login
python examples/openai_agent.py "Search Golang jobs in Shanghai above 25K"
```

## Minimal Claude (Anthropic) example

`examples/anthropic_agent.py`：

```python
"""Drive boss-agent-cli with Claude Sonnet 4.6."""
import json
import subprocess
import sys
from anthropic import Anthropic

def run_boss(*args):
    result = subprocess.run(
        ["boss", "--json", *args],
        capture_output=True, text=True, timeout=120,
    )
    return json.loads(result.stdout) if result.stdout else {"ok": False, "error": {"message": result.stderr}}


def load_tools():
    out = subprocess.run(
        ["boss", "schema", "--format", "anthropic-tools"],
        capture_output=True, text=True,
    ).stdout
    return json.loads(out)["data"]["tools"]


def main(user_prompt: str):
    client = Anthropic()
    tools = load_tools()
    messages = [{"role": "user", "content": user_prompt}]

    for _ in range(10):
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system="You are a job-hunt assistant. Use boss_* tools to operate BOSS Zhipin.",
            tools=tools,
            messages=messages,
        )

        if resp.stop_reason == "end_turn":
            for block in resp.content:
                if block.type == "text":
                    print(block.text)
            return

        if resp.stop_reason != "tool_use":
            break

        messages.append({"role": "assistant", "content": resp.content})
        tool_results = []
        for block in resp.content:
            if block.type == "tool_use":
                cmd = block.name.replace("boss_", "").replace("_", "-")
                args = block.input

                cli_args = [cmd]
                for key, value in args.items():
                    if key == "query":
                        cli_args.append(str(value))
                    else:
                        cli_args.extend([f"--{key.replace('_', '-')}", str(value)])

                output = run_boss(*cli_args)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(output, ensure_ascii=False),
                })

        messages.append({"role": "user", "content": tool_results})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "Search Python developer jobs in Beijing")
```

## Argument-mapping notes

Recommended mapping from model JSON arguments to CLI arguments:

| LLM argument | CLI form | Example |
|---------|---------|------|
| positional argument (for example `query`) | append at the start of args | `boss search python` |
| snake_case field (for example `job_id`) | convert to dashed long option | `--job-id abc123` |
| bool `true` | include as a flag | `--dry-run` |
| bool `false` | omit it (CLI defaults to false) | — |

For a more robust conversion path, reuse `_build_args()` in `mcp-server/server.py`. It already covers the full exported command surface and is a better choice than hand-maintained mappings:

```python
sys.path.insert(0, "path/to/boss-agent-cli/mcp-server")
from server import _build_args

cli_args = _build_args(call.function.name, args)
```

## Error-handling advice

boss CLI error envelope:

```json
{
  "ok": false,
  "error": {
    "code": "AUTH_EXPIRED",
    "message": "Session expired",
    "recoverable": true,
    "recovery_action": "boss login"
  }
}
```

Teach your agent to respect `recovery_action` and retry automatically when appropriate:

```python
if not output["ok"]:
    err = output["error"]
    if err.get("recoverable") and err["code"] == "AUTH_EXPIRED":
        run_boss("login")  # automatic re-login
        output = run_boss(*cli_args)  # retry once
```

## Environment prerequisites

- Python ≥ 3.10
- `boss-agent-cli` installed via `uv tool install` or `pipx install`
- `boss login` already completed so a local session exists
- OpenAI / Anthropic API keys configured via the standard SDK environment variables

## References

- [OpenAI Tools API docs](https://platform.openai.com/docs/guides/function-calling)
- [Anthropic Tool Use docs](https://docs.claude.com/en/docs/build-with-claude/tool-use)
- [`boss schema --format` options](../capability-matrix.md)
- [MCP integration guide (Claude Desktop / Cursor)](../../mcp-server/README.md)
