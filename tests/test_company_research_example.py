"""Offline caller coverage for the optional multi-MCP host example."""

import asyncio
import importlib.util
import json
from pathlib import Path

import httpx2
import pytest


def load_example():
	path = Path(__file__).resolve().parents[1] / "examples" / "company_research.py"
	spec = importlib.util.spec_from_file_location("company_research", path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def test_remote_dispatch_headers_and_private_data_boundary(monkeypatch):
	example = load_example()
	requests = []

	async def shortlist(data_dir):
		assert data_dir == Path("isolated")
		return [{"company": "Private employer", "note": "private note", "security_id": "private-id"}]

	def respond(request):
		assert str(request.url) == "https://search.parallel.ai/mcp"
		assert request.headers["user-agent"] == "boss-agent-cli/company-research"
		assert "authorization" not in request.headers
		body = json.loads(request.content)
		requests.append(body)
		method = body["method"]
		if method == "notifications/initialized":
			return httpx2.Response(202)
		if method == "initialize":
			result = {
				"protocolVersion": "2025-06-18",
				"capabilities": {"tools": {}},
				"serverInfo": {"name": "offline-parallel", "version": "1"},
			}
		elif method == "tools/list":
			result = {
				"tools": [{"name": name, "inputSchema": {"type": "object"}} for name in ("web_search", "web_fetch")]
			}
		else:
			assert method == "tools/call"
			result = {"content": [{"type": "text", "text": "Public company evidence"}]}
		return httpx2.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

	client_class = httpx2.AsyncClient

	def client(**kwargs):
		return client_class(transport=httpx2.MockTransport(respond), **kwargs)

	monkeypatch.setattr(example, "read_shortlist", shortlist)
	monkeypatch.setattr(example.httpx2, "AsyncClient", client)
	monkeypatch.setenv("PARALLEL_API_KEY", "must-not-be-used")
	result = asyncio.run(example.research("Mozilla public company", Path("isolated"), "https://www.mozilla.org"))
	assert result["shortlist"][0]["note"] == "private note"
	assert result["search"]["content"][0]["text"] == "Public company evidence"
	assert result["fetch"]["content"][0]["text"] == "Public company evidence"
	calls = [r["params"] for r in requests if r["method"] == "tools/call"]
	assert [c["name"] for c in calls] == ["web_search", "web_fetch"]
	assert calls[0]["arguments"]["search_queries"] == ["Mozilla public company"]
	assert calls[1]["arguments"]["urls"] == ["https://www.mozilla.org"]
	assert "private" not in json.dumps(requests)


def test_tool_error_is_not_success():
	example = load_example()
	from mcp.types import CallToolResult, TextContent

	with pytest.raises(RuntimeError, match="web_search"):
		example.checked_result(
			"web_search", CallToolResult(content=[TextContent(type="text", text="Rate limited")], is_error=True)
		)


def test_failure_envelope_and_no_retry(monkeypatch, capsys):
	example = load_example()
	calls = []

	async def fail(*args):
		calls.append(args)
		raise RuntimeError("offline failure")

	monkeypatch.setattr(example, "research", fail)
	assert example.main(["Mozilla company", "--data-dir", "isolated"]) == 1
	output = json.loads(capsys.readouterr().out)
	assert output["ok"] is False
	assert output["error"]["recoverable"] is True
	assert output["error"]["recovery_action"]
	assert len(calls) == 1


def test_cancellation_propagates(monkeypatch):
	example = load_example()

	async def cancelled(*args):
		raise asyncio.CancelledError()

	monkeypatch.setattr(example, "read_shortlist", cancelled)
	with pytest.raises(asyncio.CancelledError):
		asyncio.run(example.research("Mozilla company", Path("isolated")))


def test_local_stdio_dispatch_reads_selected_directory(tmp_path):
	example = load_example()
	from boss_agent_cli.cache.store import CacheStore

	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.add_shortlist(
			{
				"security_id": "fixture-id",
				"job_id": "fixture-job",
				"company": "Fixture company",
				"title": "Engineer",
				"note": "Local only",
			}
		)
	items = asyncio.run(example.read_shortlist(tmp_path))
	assert len(items) == 1
	assert items[0]["company"] == "Fixture company"
	assert items[0]["note"] == "Local only"
