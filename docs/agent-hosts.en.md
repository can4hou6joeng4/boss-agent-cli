# Agent Host Examples

An index of the smallest useful integration examples for each agent host, so `boss-agent-cli` feels copy-pasteable instead of protocol archaeology.

Current example baseline:
- Discover capabilities via `boss schema`
- Verify login state with `boss status`
- Complete the minimal action loop with `boss search` / `boss detail` / `boss greet`
- Parse `ok`, `data`, `error.code`, and `error.recovery_action` from the JSON envelope

## Example list

| Host | Good fit when | Example |
|---|---|---|
| Codex | You want to orchestrate shell commands and parse JSON directly inside the terminal | [Codex](integrations/codex.md) |
| Claude Code | You prefer skills or rule files to drive job-search actions | [Claude Code](integrations/claude-code.md) |
| Cursor | You use Composer Agent with MCP or `.cursor/rules` | [Cursor](integrations/cursor.md) |
| Windsurf | You use Cascade Agent with MCP or `.windsurfrules` | [Windsurf](integrations/windsurf.md) |
| Shell Agent | You have any shell-capable agent framework or a homegrown orchestrator | [Shell Agent](integrations/shell-agent.md) |
| Python SDK | You want business code, LangGraph, or a custom agent to call model SDKs directly | [Python SDK](integrations/python-sdk.md) |

## Picking the right host

- Choose `Codex` when the agent already has reliable terminal access and multi-step tool execution
- Choose `Claude Code` when your workflow is driven by distributed skills and rule files
- In Cursor or Windsurf, enable the MCP server first and start from the host-specific guide
- Choose `Shell Agent` for your own orchestrator or any framework that can run shell tools
- Choose `Python SDK` when the agent logic lives inside Python code and needs direct OpenAI or Claude SDK tool wiring

## Shared integration rules

1. Always start a new integration by running `boss schema`; do not hardcode the command table.
2. Treat the JSON envelope on `stdout` as the source of truth for success and failure; never parse `stderr`.
3. Use `boss doctor`, `boss login`, and `boss status` as the common recovery entry points.
4. When the user mentions benefits or welfare requirements, map them to `--welfare` first.

## Related docs

- [Recommended models and entry points](integrations/ai-models.en.md) - current setup examples for Claude 4.7, GPT-5, DeepSeek-V3, Qwen3, and more
