"""导出的本地预筛与显式空 output 口径（Issue #3097 / #3098）。

- `export` 无详情筛选的快路径只做岗位类型预筛，不跑 search / 向导共用的
  `prefilter_job`，城市 / 薪资 / 经验 / 学历不匹配的条目会被照单导出（#3097）。
- 向导 `candidate_export` 把显式空 `output` 当成缺省并写默认文件，而命令层
  `-o ""` 返回 `INVALID_PARAM`（#3098）。

全部离线：平台与登录态都是 mock。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

import boss_agent_cli.wizard.actions as actions
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import (
	ActionContext,
	DEFAULT_ACTIONS,
	_export_output_path,
)
from boss_agent_cli.wizard.runner import WorkflowActionError

# ── CLI export 快路径（#3097） ──────────────────────────────────────


def _ctx_mock(mock_cls: Any) -> Any:
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *args: None
	instance.name = "zhipin"
	instance.unwrap_data.side_effect = lambda response: response.get("zpData")
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


def _job(city: str, job_id: str) -> dict[str, Any]:
	return {
		"encryptJobId": job_id,
		"jobName": "Go 开发",
		"brandName": "演示公司",
		"salaryDesc": "20K-30K",
		"cityName": city,
		"jobExperience": "3年",
		"jobDegree": "本科",
		"securityId": f"s-{job_id}",
	}


def _invoke_export(tmp_path: Path, *args: str) -> dict[str, Any]:
	result = CliRunner().invoke(
		cli,
		["--json", "--data-dir", str(tmp_path), "export", "Go 开发", *args],
	)
	assert result.exit_code == 0, result.output
	return json.loads(result.output)


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_drops_items_failing_local_city_prefilter(mock_auth_cls, mock_client_cls, tmp_path) -> None:
	"""平台返回不满足 --city 的条目时，导出必须与 search / 向导同口径剔除。"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = {
		"zpData": {"hasMore": False, "jobList": [_job("北京", "j1")]}
	}

	envelope = _invoke_export(tmp_path, "--city", "广州", "--count", "5", "--format", "json")

	assert envelope["ok"] is True
	assert envelope["data"]["count"] == 0
	assert envelope["data"]["jobs"] == []
	assert envelope["data"]["stop_reason"] == "platform_has_more_false"


@patch("boss_agent_cli.commands.export.get_platform_instance")
@patch("boss_agent_cli.commands.export.AuthManager")
def test_export_keeps_items_matching_local_prefilter(mock_auth_cls, mock_client_cls, tmp_path) -> None:
	"""同一页里匹配与不匹配的条目混排时，只保留本地判定通过的条目。"""
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.search_jobs.return_value = {
		"zpData": {"hasMore": False, "jobList": [_job("北京", "j1"), _job("广州", "j2")]}
	}

	envelope = _invoke_export(tmp_path, "--city", "广州", "--count", "5", "--format", "json")

	assert envelope["ok"] is True
	assert envelope["data"]["count"] == 1
	assert [job["city"] for job in envelope["data"]["jobs"]] == ["广州"]
	assert envelope["data"]["stop_reason"] == "platform_has_more_false"


# ── 向导显式空 output（#3098） ─────────────────────────────────────


def _context(tmp_path: Path) -> ActionContext:
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		config=None,
	)


def _search_must_not_run(*args, **kwargs):
	raise AssertionError("平台搜索不应在参数校验失败时被调用")


@pytest.mark.parametrize("output", ["", "   ", "\t\n"])
def test_wizard_export_rejects_blank_output_before_search(tmp_path, monkeypatch, output) -> None:
	monkeypatch.setattr(actions, "execute_candidate_search", _search_must_not_run)
	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_export"](
			_context(tmp_path), {"query": "go", "output": output}, {}
		)
	assert exc_info.value.code == "INVALID_PARAM"
	assert exc_info.value.recoverable is False
	assert "output" in str(exc_info.value)


@pytest.mark.parametrize("inputs", [{"query": "go"}, {"query": "go", "output": None}])
def test_wizard_export_default_output_path_only_for_missing_or_null(tmp_path, inputs) -> None:
	assert _export_output_path(_context(tmp_path), inputs, "json") == (
		tmp_path / "exports" / "wizard-jobs.json"
	)
