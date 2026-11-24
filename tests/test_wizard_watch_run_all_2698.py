"""向导 candidate_watch 的 run 支持 all=true 批量运行（Issue #2698）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from boss_agent_cli.api.client import AccountRiskError
from boss_agent_cli.output import Logger
from boss_agent_cli.search_filters import SearchPipelinePlatformError
from boss_agent_cli.wizard import actions
from boss_agent_cli.wizard.actions import DEFAULT_ACTIONS, ActionContext
from boss_agent_cli.wizard.models import WorkflowInputError
from boss_agent_cli.wizard.runner import WorkflowActionError


class _Platform:
	def __enter__(self) -> "_Platform":
		return self

	def __exit__(self, *exc: object) -> None:
		return None


class _Result:
	def __init__(self, items: list[dict[str, Any]]) -> None:
		self.items = items


def _context(tmp_path: Path, *, forbid_platform: bool = False) -> ActionContext:
	def factory(name: str, auth: Any, delay: Any, cdp_url: Any, browser_source: Any = None) -> Any:
		if forbid_platform:
			raise AssertionError("不应构造平台实例")
		return _Platform()

	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		candidate_factory=factory,
		recruiter_factory=factory,
	)


def _add(context: ActionContext, *names: str) -> None:
	for name in names:
		DEFAULT_ACTIONS["candidate_watch"](context, {"action": "add", "name": name, "query": f"kw-{name}"}, {})


def _run_all(context: ActionContext, search: Any, **extra: Any) -> Any:
	with (
		patch.object(actions, "execute_candidate_search", side_effect=search),
		patch.object(actions, "account_scope", return_value="acct"),
	):
		return DEFAULT_ACTIONS["candidate_watch"](context, {"action": "run", "all": True, **extra}, {})


def _job(query: str) -> dict[str, Any]:
	return {"security_id": f"sid-{query}", "job_id": f"job-{query}", "title": query}


def test_run_all_runs_every_watch(tmp_path: Path) -> None:
	context = _context(tmp_path)
	_add(context, "a", "b")
	seen: list[str] = []

	def search(platform: Any, cache: Any, logger: Any, inputs: Any) -> _Result:
		seen.append(inputs["query"])
		return _Result([_job(inputs["query"])])

	data = _run_all(context, search).data
	assert sorted(seen) == ["kw-a", "kw-b"]
	assert data["mode"] == "all"
	assert data["total"] == 2
	assert sorted(item["name"] for item in data["watches"]) == ["a", "b"]
	assert all(item["new_count"] == 1 for item in data["watches"])
	assert data["skipped"] == []
	assert data["invalid"] == []


def test_run_all_without_watches_does_not_build_platform(tmp_path: Path) -> None:
	data = _run_all(_context(tmp_path, forbid_platform=True), lambda *a, **k: _Result([])).data
	assert data == {"mode": "all", "watches": [], "total": 0, "skipped": [], "invalid": []}


def test_run_all_reports_corrupt_and_invalid_records_and_continues(tmp_path: Path) -> None:
	context = _context(tmp_path)
	_add(context, "bad-json", "blank", "good")
	conn = sqlite3.connect(tmp_path / "cache" / "boss_agent.db")
	conn.execute("UPDATE saved_searches SET params = ? WHERE name = ?", ("{not json", "bad-json"))
	conn.execute("UPDATE saved_searches SET params = ? WHERE name = ?", ('{"query": "  "}', "blank"))
	conn.commit()
	conn.close()

	data = _run_all(context, lambda p, c, log, inputs: _Result([_job(inputs["query"])])).data
	assert data["skipped"] == ["bad-json"]
	assert data["invalid"] == ["blank"]
	by_name = {item["name"]: item for item in data["watches"]}
	assert by_name["blank"]["error"] == "SAVED_SEARCH_INVALID"
	assert by_name["good"]["new_count"] == 1
	assert data["total"] == 2


def test_run_all_keeps_non_risk_platform_error_as_entry(tmp_path: Path) -> None:
	context = _context(tmp_path)
	_add(context, "a", "b")
	calls: list[str] = []

	def search(platform: Any, cache: Any, logger: Any, inputs: Any) -> _Result:
		calls.append(inputs["query"])
		if len(calls) == 1:
			raise SearchPipelinePlatformError("RATE_LIMITED", "请求过于频繁")
		return _Result([])

	data = _run_all(context, search).data
	assert len(calls) == 2
	assert [item.get("error") for item in data["watches"]] == ["RATE_LIMITED", None]
	assert data["invalid"] == []


@pytest.mark.parametrize(
	"error",
	[
		SearchPipelinePlatformError("ACCOUNT_RISK", "平台原文 token=abc"),
		AccountRiskError("平台原文 token=abc"),
	],
)
def test_run_all_stops_on_risk_without_further_requests(tmp_path: Path, error: Exception) -> None:
	context = _context(tmp_path)
	_add(context, "a", "b", "c")
	calls: list[str] = []

	def search(platform: Any, cache: Any, logger: Any, inputs: Any) -> _Result:
		calls.append(inputs["query"])
		if len(calls) == 2:
			raise error
		return _Result([])

	with pytest.raises(WorkflowActionError) as exc_info:
		_run_all(context, search)
	exc = exc_info.value
	assert len(calls) == 2
	assert exc.code == "ACCOUNT_RISK"
	assert exc.recoverable is False
	assert "token=abc" not in str(exc)
	assert exc.details is not None
	assert len(exc.details["watches"]) == 1
	assert exc.details["stopped_watch"] in {"a", "b", "c"}
	assert exc.details["stopped_watch"] != exc.details["watches"][0]["name"]


def test_run_all_rejects_name_before_any_platform_use(tmp_path: Path) -> None:
	context = _context(tmp_path, forbid_platform=True)
	with pytest.raises(WorkflowActionError) as exc_info:
		_run_all(context, lambda *a, **k: _Result([]), name="a")
	assert exc_info.value.code == "INVALID_PARAM"
	assert "name" in str(exc_info.value)


@pytest.mark.parametrize("action", ["add", "remove"])
def test_all_rejected_for_other_actions(tmp_path: Path, action: str) -> None:
	context = _context(tmp_path, forbid_platform=True)
	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_watch"](context, {"action": action, "all": True, "name": "a", "query": "x"}, {})
	assert exc_info.value.code == "INVALID_PARAM"
	assert DEFAULT_ACTIONS["candidate_watch"](context, {"action": "list"}, {}).data["total"] == 0


@pytest.mark.parametrize("raw", ["true", 1, "false", []])
def test_all_must_be_json_boolean(tmp_path: Path, raw: Any) -> None:
	context = _context(tmp_path, forbid_platform=True)
	with pytest.raises((WorkflowInputError, WorkflowActionError), match="all"):
		DEFAULT_ACTIONS["candidate_watch"](context, {"action": "run", "all": raw}, {})


def test_all_false_keeps_named_run(tmp_path: Path) -> None:
	context = _context(tmp_path)
	_add(context, "a")
	with (
		patch.object(actions, "execute_candidate_search", return_value=_Result([_job("x")])),
		patch.object(actions, "account_scope", return_value="acct"),
	):
		data = DEFAULT_ACTIONS["candidate_watch"](context, {"action": "run", "all": False, "name": "a"}, {}).data
	assert data["name"] == "a"
	assert data["new_count"] == 1
	assert "mode" not in data


def test_tty_renders_batch_result_with_literal_names() -> None:
	import io

	from rich.console import Console

	from boss_agent_cli import display
	from boss_agent_cli.wizard.renderer import _render_management_result

	buffer = io.StringIO()
	run = {
		"goal": "watch",
		"role": "candidate",
		"last_result": {
			"data": {
				"mode": "all",
				"watches": [
					{"name": "[/bold]日报", "new_count": 3},
					{"name": "失效", "error": "SAVED_SEARCH_INVALID", "message": "x"},
				],
				"total": 2,
				"skipped": ["坏记录"],
				"invalid": ["失效"],
			}
		},
	}
	with patch.object(display, "console", Console(file=buffer, width=100, force_terminal=False)):
		assert _render_management_result(run) is True
	text = buffer.getvalue()
	assert "[/bold]日报" in text
	assert "SAVED_SEARCH_INVALID" in text
	assert "坏记录" in text
