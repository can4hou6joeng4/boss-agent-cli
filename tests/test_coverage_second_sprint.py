"""二次覆盖率冲刺：覆盖 logout/show/mark/me/match_score/pipeline_state 的散点分支。"""

import json
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	return instance


# ── logout 异常路径 ───────────────────────────────────────


@patch("boss_agent_cli.commands.logout.AuthManager")
def test_logout_handles_exception(mock_auth_cls):
	mock_auth_cls.return_value.logout.side_effect = OSError("disk full")
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "logout"])
	# emit_error 不主动 exit，输出 JSON 后正常返回
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "NETWORK_ERROR"


# ── show 错误分支 ──────────────────────────────────────────


@patch("boss_agent_cli.commands.show.get_index_info")
@patch("boss_agent_cli.commands.show.get_job_by_index")
def test_show_index_out_of_range(mock_get_job, mock_get_info):
	mock_get_job.return_value = None
	mock_get_info.return_value = {"exists": True, "count": 5, "source": "search"}
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "show", "99"])
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert "超出范围" in parsed["error"]["message"]


@patch("boss_agent_cli.commands.show.get_index_info")
@patch("boss_agent_cli.commands.show.get_job_by_index")
def test_show_no_cached_results(mock_get_job, mock_get_info):
	mock_get_job.return_value = None
	mock_get_info.return_value = {"exists": False, "count": 0, "source": None}
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "show", "1"])
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert "没有缓存的搜索结果" in parsed["error"]["message"]


@patch("boss_agent_cli.commands.show.get_job_by_index")
def test_show_missing_security_id(mock_get_job):
	mock_get_job.return_value = {"title": "Go", "company": "X"}  # 故意无 security_id
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "show", "1"])
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert "security_id" in parsed["error"]["message"]


@patch("boss_agent_cli.commands.show.get_platform_instance")
@patch("boss_agent_cli.commands.show.AuthManager")
@patch("boss_agent_cli.commands.show.get_job_by_index")
def test_show_job_card_empty_returns_not_found(mock_get_job, mock_auth_cls, mock_client_cls):
	mock_get_job.return_value = {"security_id": "sec1"}
	client = _ctx_mock(mock_client_cls)
	client.job_card.return_value = {"zpData": {"jobCard": {}}}  # 空 card
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "show", "1"])
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "JOB_NOT_FOUND"


# ── mark 错误分支和 _resolve_label ───────────────────────


def test_mark_resolve_label_by_numeric_id():
	from boss_agent_cli.commands.mark import _resolve_label
	assert _resolve_label("11") == 11
	assert _resolve_label("3") == 3


def test_mark_resolve_label_by_fuzzy_match():
	from boss_agent_cli.commands.mark import _resolve_label
	assert _resolve_label("招呼") == 1  # 模糊匹配"新招呼"
	assert _resolve_label("面") == 3  # 模糊匹配"已约面"


def test_mark_resolve_label_unknown_raises():
	import click
	import pytest
	from boss_agent_cli.commands.mark import _resolve_label
	with pytest.raises(click.BadParameter):
		_resolve_label("火星标签")


@patch("boss_agent_cli.commands.mark.BossClient")
@patch("boss_agent_cli.commands.mark.AuthManager")
def test_mark_security_id_not_found_in_friend_list(mock_auth_cls, mock_client_cls):
	client = _ctx_mock(mock_client_cls)
	client.friend_list.return_value = {
		"zpData": {"result": [{"securityId": "other", "uid": 1}]},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "mark", "ghost", "--label", "收藏"])
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "JOB_NOT_FOUND"


# ── me 异常分支 ──────────────────────────────────────────


@patch("boss_agent_cli.commands.me.get_platform_instance")
@patch("boss_agent_cli.commands.me.AuthManager")
def test_me_auth_required(mock_auth_cls, mock_client_cls):
	from boss_agent_cli.auth.manager import AuthRequired
	mock_client_cls.side_effect = AuthRequired()
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "me"])
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "AUTH_REQUIRED"


@patch("boss_agent_cli.commands.me.get_platform_instance")
@patch("boss_agent_cli.commands.me.AuthManager")
def test_me_auth_error(mock_auth_cls, mock_client_cls):
	from boss_agent_cli.api.client import AuthError
	mock_client_cls.side_effect = AuthError("expired")
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "me"])
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "AUTH_EXPIRED"


@patch("boss_agent_cli.commands.me.get_platform_instance")
@patch("boss_agent_cli.commands.me.AuthManager")
def test_me_generic_exception(mock_auth_cls, mock_client_cls):
	mock_client_cls.side_effect = RuntimeError("boom")
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "me"])
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "NETWORK_ERROR"


# ── match_score 边界分支 ─────────────────────────────────


def test_match_score_salary_required_none_returns_zero():
	from boss_agent_cli.api.models import JobItem
	from boss_agent_cli.match_score import score_job_item
	from boss_agent_cli.search_filters import SearchFilterCriteria

	job = JobItem(
		job_id="j1", title="Go", company="X", salary="20-30K", city="北京",
		district="", experience="", education="", skills=[], welfare=[],
		industry="", scale="", stage="", boss_name="", boss_title="",
		boss_active="", security_id="s1", greeted=False,
	)
	# criteria 的 salary 为 None，expect_data 也无 salary → 薪资评分 return 0
	result = score_job_item(
		job,
		criteria=SearchFilterCriteria(query="Go"),
		expect_data={},
	)
	assert "薪资满足预期" not in result["match_reasons"]


def test_match_score_salary_unparseable_returns_zero():
	from boss_agent_cli.api.models import JobItem
	from boss_agent_cli.match_score import score_job_item
	from boss_agent_cli.search_filters import SearchFilterCriteria

	job = JobItem(
		job_id="j1", title="Go", company="X", salary="薪资面议", city="北京",
		district="", experience="", education="", skills=[], welfare=[],
		industry="", scale="", stage="", boss_name="", boss_title="",
		boss_active="", security_id="s1", greeted=False,
	)
	# candidate_range 无法解析，应 return 0（不加入匹配原因）
	result = score_job_item(
		job,
		criteria=SearchFilterCriteria(query="", salary="20-30K"),
		expect_data={},
	)
	assert "薪资满足预期" not in result["match_reasons"]
	assert "薪资低于预期" not in result["mismatch_reasons"]


def test_match_score_experience_mismatch():
	from boss_agent_cli.api.models import JobItem
	from boss_agent_cli.match_score import score_job_item
	from boss_agent_cli.search_filters import SearchFilterCriteria

	job = JobItem(
		job_id="j1", title="Go", company="X", salary="20-30K", city="北京",
		district="", experience="1-3年", education="", skills=[], welfare=[],
		industry="", scale="", stage="", boss_name="", boss_title="",
		boss_active="", security_id="s1", greeted=False,
	)
	result = score_job_item(
		job,
		criteria=SearchFilterCriteria(query="", experience="5-10年"),
		expect_data={},
	)
	assert "经验低于要求" in result["mismatch_reasons"]


def test_match_score_education_mismatch():
	from boss_agent_cli.api.models import JobItem
	from boss_agent_cli.match_score import score_job_item
	from boss_agent_cli.search_filters import SearchFilterCriteria

	job = JobItem(
		job_id="j1", title="Go", company="X", salary="20-30K", city="北京",
		district="", experience="", education="大专", skills=[], welfare=[],
		industry="", scale="", stage="", boss_name="", boss_title="",
		boss_active="", security_id="s1", greeted=False,
	)
	result = score_job_item(
		job,
		criteria=SearchFilterCriteria(query="", education="硕士"),
		expect_data={},
	)
	assert "学历低于要求" in result["mismatch_reasons"]


# ── pipeline_state 边界分支 ──────────────────────────────


def test_pipeline_ts_to_label_zero_returns_dash():
	from boss_agent_cli.pipeline_state import _ts_to_label
	assert _ts_to_label(0) == "-"


def test_pipeline_chat_stage_chatting_when_recent():
	"""最新消息在 stale_days 窗口内，无未读，非 applied → chatting"""
	from boss_agent_cli.pipeline_state import _chat_stage
	now_ts = 1700000000000
	item = {
		"unreadMsgCount": 0,
		"relationType": 1,
		"lastTS": now_ts - 1000,  # 1 秒前
	}
	assert _chat_stage(item, now_ts_ms=now_ts, stale_days=3) == "chatting"
