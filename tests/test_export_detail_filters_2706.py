"""CLI / MCP export 支持福利与活跃度筛选（Issue #2706）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.search_filters import (
	SearchPipelinePlatformError,
	SearchPipelineResult,
	SearchPipelineStats,
	resolve_welfare_keywords,
)
from boss_agent_cli.search_filters import detail_filter_max_pages
from boss_agent_cli.services.pagination import export_page_budget
from boss_agent_cli.schema.data import SCHEMA_DATA
from boss_agent_cli.schema.tool_catalog import build_tool_catalog


def _result(
	items: list[dict[str, Any]],
	*,
	stop_reason: str = "limit_reached",
	unknown: dict[str, int] | None = None,
	detail_requests: int = 0,
	active_rejected: int = 0,
	active_unknown: int = 0,
) -> SearchPipelineResult:
	stats = SearchPipelineStats(
		detail_requests=detail_requests,
		active_rejected=active_rejected,
		active_unknown=active_unknown,
	)
	return SearchPipelineResult(
		items=items,
		stop_reason=stop_reason,
		stats=stats,
		active_unknown_descs=unknown or {},
	)


def _job(title: str = "Go 开发") -> dict[str, Any]:
	return {
		"job_id": "j1",
		"title": title,
		"company": "TestCo",
		"salary": "20K",
		"city": "广州",
		"experience": "3-5年",
		"education": "本科",
		"skills": ["Go"],
		"welfare": ["双休"],
		"security_id": "sec",
	}


def _invoke(tmp_path: Path, args: list[str]) -> Any:
	return CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "export", "golang", *args], catch_exceptions=False
	)


@patch("boss_agent_cli.commands.export.run_search_pipeline")
@patch("boss_agent_cli.commands.export.account_scope", return_value="acct")
@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_welfare_active_uses_pipeline(
	mock_auth: Any, mock_platform: Any, _account: Any, mock_pipeline: Any, tmp_path: Path
) -> None:
	platform = mock_platform.return_value
	platform.__enter__ = lambda self: self
	platform.__exit__ = lambda self, *a: None
	platform.name = "zhipin"
	mock_pipeline.return_value = _result(
		[_job()], unknown={"(空)": 2}, detail_requests=4, active_unknown=2, active_rejected=1
	)
	out = tmp_path / "jobs.csv"

	result = _invoke(
		tmp_path,
		["--welfare", "双休,五险一金", "--active", "3day", "--count", "2", "-o", str(out)],
	)

	assert result.exit_code == 0, result.output
	envelope = json.loads(result.output)
	assert envelope["data"]["count"] == 1
	# 管线没有 natural stop：limit_reached 映射回 export 既有词汇 count_reached。
	assert envelope["data"]["stop_reason"] == "count_reached"
	assert envelope["data"]["truncated"] is False
	assert envelope["hints"]["active_filter"] == {
		"level": "3d",
		"rejected": 1,
		"unknown_excluded": 2,
		"unknown_descs": {"(空)": 2},
		"note": "活跃度文案无法识别或详情未返回的职位已排除",
		"detail_lookups": 4,
		"detail_note": (
			"列表看不出活跃度，逐个查了 4 个职位详情；"
			"非 online 档位更慢、风控风险更高，低风险场景建议用 --active online"
		),
	}
	assert "Go 开发" in out.read_text(encoding="utf-8")

	kwargs = mock_pipeline.call_args.kwargs
	assert kwargs["active"] == "3d"
	assert kwargs["welfare_conditions"] == [
		("双休", resolve_welfare_keywords("双休")),
		("五险一金", resolve_welfare_keywords("五险一金")),
	]
	assert kwargs["limit"] == 2
	assert kwargs["account"] == "acct"
	assert kwargs["continue_when_has_more_unknown"] is True
	# 详情筛选的页数预算不得超过 search 的详情上限（welfare 默认 5 页）。
	assert kwargs["max_pages"] == min(export_page_budget(2), detail_filter_max_pages(kwargs["welfare_conditions"], "3d"))


@patch("boss_agent_cli.commands.export.run_search_pipeline")
@patch("boss_agent_cli.commands.export.account_scope", return_value="")
@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_html_from_pipeline_items(
	mock_auth: Any, mock_platform: Any, _account: Any, mock_pipeline: Any, tmp_path: Path
) -> None:
	platform = mock_platform.return_value
	platform.__enter__ = lambda self: self
	platform.__exit__ = lambda self, *a: None
	platform.name = "zhipin"
	mock_pipeline.return_value = _result([_job()], stop_reason="empty_page")
	out = tmp_path / "jobs.html"

	result = _invoke(tmp_path, ["--welfare", "双休", "--format", "html", "-o", str(out)])

	assert result.exit_code == 0, result.output
	html = out.read_text(encoding="utf-8")
	assert "Go 开发" in html
	assert "TestCo" in html
	# HTML 契约省略薪资，即使管线条目里带着。
	assert "20K" not in html
	assert json.loads(result.output)["data"]["stop_reason"] == "empty_page"


@patch("boss_agent_cli.commands.export.run_search_pipeline")
@patch("boss_agent_cli.commands.export.account_scope", return_value="")
@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_page_budget_truncation(
	mock_auth: Any, mock_platform: Any, _account: Any, mock_pipeline: Any, tmp_path: Path
) -> None:
	platform = mock_platform.return_value
	platform.__enter__ = lambda self: self
	platform.__exit__ = lambda self, *a: None
	platform.name = "zhipin"
	mock_pipeline.return_value = _result([_job()], stop_reason="page_budget_exhausted")

	result = _invoke(tmp_path, ["--active", "online"])

	envelope = json.loads(result.output)
	assert envelope["data"]["truncated"] is True
	assert envelope["data"]["stop_reason"] == "page_budget_exhausted"
	assert envelope["hints"]["active_filter"]["level"] == "online"


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_rejects_unknown_active_before_platform(mock_auth: Any, mock_platform: Any, tmp_path: Path) -> None:
	mock_platform.side_effect = AssertionError("不应构造平台实例")
	result = _invoke(tmp_path, ["--active", "never"])
	assert result.exit_code == 1
	envelope = json.loads(result.output)
	assert envelope["error"]["code"] == "INVALID_PARAM"
	assert mock_platform.call_count == 0


@patch("boss_agent_cli.commands.export.run_search_pipeline")
@patch("boss_agent_cli.commands.export.account_scope", return_value="")
@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_pipeline_risk_error_keeps_contract(
	mock_auth: Any, mock_platform: Any, _account: Any, mock_pipeline: Any, tmp_path: Path
) -> None:
	platform = mock_platform.return_value
	platform.__enter__ = lambda self: self
	platform.__exit__ = lambda self, *a: None
	platform.name = "zhipin"
	mock_pipeline.side_effect = SearchPipelinePlatformError("ACCOUNT_RISK", "平台原文 token=abc")

	result = _invoke(tmp_path, ["--active", "3d"])

	assert result.exit_code == 1
	envelope = json.loads(result.output)
	assert envelope["error"]["code"] == "ACCOUNT_RISK"
	assert envelope["error"]["recoverable"] is False
	assert "token=abc" not in json.dumps(envelope, ensure_ascii=False)


def test_schema_and_mcp_expose_export_filters() -> None:
	options = SCHEMA_DATA["commands"]["export"]["options"]
	assert options["--active"]["choices"] == ["online", "today", "3d", "week", "2w", "month", "half-year"]
	assert options["--welfare"]["type"] == "string"

	assert _build_args("export", {"query": "x", "welfare": "双休", "active": "3d"}) == [
		"export",
		"x",
		"--welfare",
		"双休",
		"--active",
		"3d",
	]
	tool = next(tool for tool in build_tool_catalog() if tool.name == "boss_export")
	properties = tool.input_schema["properties"]
	assert properties["welfare"]["type"] == "string"
	assert properties["active"]["type"] == "string"
