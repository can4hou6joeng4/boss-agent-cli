# Company research with two MCP servers

Use the runnable [Python example](../../examples/company_research.py) to display
your local job shortlist alongside public company research for interview preparation.
It calls `boss_shortlist_list` over stdio and
[Parallel Search MCP](https://docs.parallel.ai/integrations/mcp/search-mcp) over
Streamable HTTP. Search uses Fast mode; the anonymous endpoint needs no account
or API key and has lower rate limits. An optional URL enables page fetching.

## Install and run

From a source checkout, create a separate environment with the example's dependencies:

```bash
uv venv .venv-research
uv pip install --python .venv-research/bin/python ".[mcp]" -r examples/company-research-requirements.txt
export PATH="$PWD/.venv-research/bin:$PATH"
.venv-research/bin/python examples/company_research.py \
  "Mozilla company products careers" --data-dir ./.boss-agent
```

On Windows, use `.venv-research\Scripts\python.exe` for both Python paths above.
The installed `boss` executable must be on `PATH` because the MCP server invokes
the CLI. On POSIX, prepend the environment's bin directory before running:

```bash
export PATH="$PWD/.venv-research/bin:$PATH"
.venv-research/bin/python examples/company_research.py \
  "Mozilla company products careers" --data-dir ./.boss-agent \
  --url https://www.mozilla.org/en-US/about/
```

On Windows PowerShell, prepend `$PWD\.venv-research\Scripts` to `$env:PATH`.
No BOSS login is needed to read the local shortlist. Use the data directory where
you saved jobs; an empty directory returns an empty shortlist. Existing `boss`
commands, AI providers and MCP host configurations are unchanged.

## Output and boundaries

The example makes direct tool calls without a model or an autonomous agent loop.
Stdout contains one JSON envelope. `data.shortlist` contains local items,
`data.search` contains the MCP search result with source URLs and excerpts, and
`data.fetch` is present only with `--url`. MCP content and structured results are
preserved. Exit status is 0 for success or 1 for a connection/tool failure; the
failure envelope includes a recovery action and stderr carries diagnostics.

Only the explicit public query and optional URL are sent to Parallel. The example
does not derive queries from your shortlist or send resumes, notes, job identifiers,
cookies or saved API keys. Keep private information out of those arguments.
The combined stdout includes local shortlist fields, so redact it before sharing.
Requests have a 60-second timeout and the complete workflow is bounded to 180 seconds.
Failures stop without automatic retries; retry later if the anonymous endpoint is
rate limited. Review source pages before relying on web results.
