"""审计批次六十三：MCP action 默认值 / shortlist 互斥 / availability 入参（Issue #2339–#2341）。

- #2339：映射层把「等于 schema 默认值的越界参数」按缺省处理；wizard/config 的
  inputSchema 显式禁止 run 带 run_id / list 带 key，与映射层同源；
- #2340：shortlist 的 selectors/all 互斥按值判断，`all=false` 与 selectors 并存
  不再被拒；schema 的 not 条件同步改为值条件；
- #2341：availability 的 hr 分支从传入 data 读 subcommands，不再回落全局
  SCHEMA_DATA；supported_recruiter_platforms 非 list 归一为空列表。

全部离线：纯映射函数与 schema 数据，不发平台请求。
"""

from __future__ import annotations

import copy

import pytest

from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.schema.availability import availability_note, inject_availability
from boss_agent_cli.schema.data import SCHEMA_DATA
from boss_agent_cli.schema.tool_catalog import build_tool_catalog


# ── #2339：schema 默认值与 action 互斥同源 ───────────────────────


def test_hr_jobs_ignores_schema_default_page_for_online():
	args = _build_args("boss_hr_jobs", {"action": "online", "job_id": "j1", "page": 1})

	assert args == ["hr", "jobs", "online", "j1"]


def test_hr_jobs_still_rejects_non_default_page_for_online():
	with pytest.raises(ValueError, match="page"):
		_build_args("boss_hr_jobs", {"action": "online", "job_id": "j1", "page": 2})


def test_hr_jobs_still_rejects_other_action_args():
	with pytest.raises(ValueError, match="job_id"):
		_build_args("boss_hr_jobs", {"action": "list", "job_id": "j1"})


def test_config_list_with_key_is_rejected_and_schema_forbids_it():
	with pytest.raises(ValueError, match="key"):
		_build_args("boss_config", {"action": "list", "key": "x"})

	tools = {tool.name: tool for tool in _catalog()}
	config = tools["boss_config"].input_schema
	assert config["else"] == {"not": {"required": ["key"]}}


def test_wizard_run_with_run_id_is_rejected_and_schema_forbids_it():
	with pytest.raises(ValueError, match="run_id"):
		_build_args(
			"boss_wizard",
			{"action": "run", "role": "candidate", "platform": "zhipin", "goal": "g", "run_id": "r1"},
		)

	tools = {tool.name: tool for tool in _catalog()}
	wizard = tools["boss_wizard"].input_schema
	assert wizard["else"]["not"] == {"required": ["run_id"]}
	assert wizard["else"]["required"] == ["role", "platform", "goal"]


def _catalog():
	data = inject_availability(copy.deepcopy(SCHEMA_DATA))
	return build_tool_catalog(data)


# ── #2340：shortlist 互斥按值 ────────────────────────────────────


def test_shortlist_accepts_all_false_with_selectors():
	args = _build_args(
		"boss_crawl_shortlist", {"run_id": "r1", "selectors": ["x"], "all": False},
	)

	assert args == ["crawl", "shortlist", "r1", "--selector", "x"]


def test_shortlist_accepts_all_true_without_selectors():
	args = _build_args("boss_crawl_shortlist", {"run_id": "r1", "all": True})

	assert args == ["crawl", "shortlist", "r1", "--all"]


def test_shortlist_rejects_all_true_with_selectors():
	with pytest.raises(ValueError, match="selectors"):
		_build_args(
			"boss_crawl_shortlist", {"run_id": "r1", "selectors": ["x"], "all": True},
		)


def test_shortlist_schema_not_is_value_based():
	tools = {tool.name: tool for tool in _catalog()}
	shortlist = tools["boss_crawl_shortlist"].input_schema

	assert shortlist["not"] == {
		"required": ["selectors"],
		"properties": {"all": {"const": True}},
	}


# ── #2341：availability 使用传入 data ───────────────────────────


def test_hr_subcommands_come_from_passed_data():
	data = {
		"commands": {"hr": {"subcommands": {"only-one": "x"}}},
		"supported_recruiter_platforms": ["zhipin-recruiter"],
	}

	out = inject_availability(data)

	assert sorted(out["commands"]["hr"]["availability"]["subcommands"]) == ["only-one"]


def test_availability_note_survives_non_list_platforms():
	data = {"commands": {"hr": {"subcommands": {"only-one": "x"}}}, "supported_recruiter_platforms": None}

	out = inject_availability(data)
	availability = out["commands"]["hr"]["availability"]

	assert availability["recruiter_platforms"] == []
	assert "zhipin-recruiter" not in availability_note(availability)


def test_inject_availability_tolerates_missing_commands():
	out = inject_availability({"supported_recruiter_platforms": []})

	assert out["commands"] == {}
