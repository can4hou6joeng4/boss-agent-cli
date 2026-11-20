"""二次覆盖率冲刺：覆盖 logout/show/mark/me/match_score/pipeline_state 的散点分支。"""

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli

_DEFAULT_DATA_DIR = str((Path.home() / ".boss-agent").resolve())


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") if "zpData" in response else response.get("data")
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
	assert parsed["error"]["code"] == "LOGOUT_FAILED"
	assert parsed["error"]["recoverable"] is True
	assert "boss logout" in parsed["error"]["recovery_action"]


# ── show 错误分支 ──────────────────────────────────────────


@patch("boss_agent_cli.commands.show.get_index_entry")
def test_show_index_out_of_range(mock_get_entry):
	mock_get_entry.return_value = ({"exists": True, "count": 5, "source": "search", "page": None, "saved_at": 0}, None)
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "show", "99"])
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert "超出范围" in parsed["error"]["message"]
	assert "结果数以内的编号" in parsed["error"]["recovery_action"]
	assert parsed["hints"]["next_actions"] == [
		f"boss --data-dir {_DEFAULT_DATA_DIR} search <关键词>",
		f"boss --data-dir {_DEFAULT_DATA_DIR} recommend",
	]


@patch("boss_agent_cli.commands.show.get_index_entry")
def test_show_non_positive_index_rejected_before_cache(mock_get_entry):
	"""编号从 1 开始：0 / 负数不该被引去先 search 一次，等回来还是同一个错误。"""
	runner = CliRunner()
	for argv in (["0"], ["--", "-3"]):
		result = runner.invoke(cli, ["--json", "show", *argv])
		parsed = json.loads(result.output)
		assert result.exit_code == 1
		assert parsed["ok"] is False
		assert parsed["error"]["code"] == "INVALID_PARAM"
		assert "编号必须从 1 开始" in parsed["error"]["message"]
		assert parsed["hints"]["next_actions"] == [
			f"boss --data-dir {_DEFAULT_DATA_DIR} search <关键词>",
			f"boss --data-dir {_DEFAULT_DATA_DIR} recommend",
		]
	mock_get_entry.assert_not_called()


@patch("boss_agent_cli.commands.show.get_index_entry")
def test_show_no_cached_results(mock_get_entry):
	mock_get_entry.return_value = ({"exists": False, "count": 0, "source": None}, None)
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "show", "1"])
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert "没有缓存的搜索结果" in parsed["error"]["message"]
	assert "建立结果缓存" in parsed["error"]["recovery_action"]
	assert parsed["hints"]["next_actions"] == [
		f"boss --data-dir {_DEFAULT_DATA_DIR} search <关键词>",
		f"boss --data-dir {_DEFAULT_DATA_DIR} recommend",
	]


@patch("boss_agent_cli.commands.show.get_index_entry")
def test_show_missing_security_id(mock_get_entry):
	# 故意无 security_id
	mock_get_entry.return_value = (
		{"exists": True, "count": 1, "source": "search:x", "page": None, "saved_at": 0},
		{"title": "Go", "company": "X"},
	)
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "show", "1"])
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert "security_id" in parsed["error"]["message"]
	assert "重新搜索后再查看" in parsed["error"]["recovery_action"]
	assert parsed["hints"]["next_actions"] == [
		f"boss --data-dir {_DEFAULT_DATA_DIR} search <关键词>",
		f"boss --data-dir {_DEFAULT_DATA_DIR} recommend",
	]


@patch("boss_agent_cli.commands.show.get_platform_instance")
@patch("boss_agent_cli.commands.show.AuthManager")
@patch("boss_agent_cli.commands.show.get_index_entry")
def test_show_job_card_empty_returns_not_found(mock_get_entry, mock_auth_cls, mock_client_cls):
	mock_get_entry.return_value = (
		{"exists": True, "count": 1, "source": "search:x", "page": None, "saved_at": 0},
		{"security_id": "sec1"},
	)
	client = _ctx_mock(mock_client_cls)
	client.job_card.return_value = {"zpData": {"jobCard": {}}}  # 空 card
	client.unwrap_data.return_value = client.job_card.return_value["zpData"]
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "show", "1"])
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "JOB_NOT_FOUND"
	# 「下架」不是死胡同：恢复指引与下一步必须能直接换线索。
	assert parsed["error"]["recovery_action"]
	assert parsed["hints"]["next_actions"] == [
		f"boss --data-dir {_DEFAULT_DATA_DIR} search <关键词>",
		f"boss --data-dir {_DEFAULT_DATA_DIR} recommend",
	]


# ── mark 错误分支和 _resolve_label ───────────────────────


def test_mark_resolve_label_by_numeric_id():
	from boss_agent_cli.commands.mark import _resolve_label
	assert _resolve_label("11") == 11
	assert _resolve_label("3") == 3


def test_mark_resolve_label_rejects_fuzzy_match():
	import click
	import pytest
	from boss_agent_cli.commands.mark import _resolve_label
	# 子串曾会模糊命中标签并触发真实写操作，现已与 schema enum 收口（Issue #1615）。
	for value in ("招呼", "面", "已交换"):
		with pytest.raises(click.BadParameter):
			_resolve_label(value)


def test_mark_resolve_label_unknown_raises():
	import click
	import pytest
	from boss_agent_cli.commands.mark import _resolve_label
	with pytest.raises(click.BadParameter):
		_resolve_label("火星标签")


@patch("boss_agent_cli.commands.mark.get_platform_instance")
@patch("boss_agent_cli.commands.mark.AuthManager")
def test_mark_security_id_not_found_in_friend_list(mock_auth_cls, mock_client_cls):
	client = _ctx_mock(mock_client_cls)
	client.friend_list.return_value = {
		"zpData": {"result": [{"securityId": "other", "uid": 1}]},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "mark", "ghost", "--label", "收藏"])
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "CONTACT_NOT_FOUND"


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


@patch("boss_agent_cli.commands.me.get_platform_instance")
@patch("boss_agent_cli.commands.me.AuthManager")
def test_me_cache_busy_is_not_network_error(mock_auth_cls, mock_client_cls):
	"""本地缓存锁冲突不是平台网络问题：等锁释放即可（Issue #1464）。"""
	from boss_agent_cli.cache.store import CacheBusyError

	platform = _ctx_mock(mock_client_cls)
	platform.user_info.side_effect = CacheBusyError(Path("/tmp/synthetic-cache.db"), "database is locked")
	result = CliRunner().invoke(cli, ["--json", "me"])
	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "CACHE_BUSY"
	assert parsed["error"]["recoverable"] is True
	assert "等待占用" in parsed["error"]["recovery_action"]


@patch("boss_agent_cli.commands.me.get_platform_instance")
@patch("boss_agent_cli.commands.me.AuthManager")
def test_me_raw_sqlite_busy_operational_error_is_cache_busy(mock_auth_cls, mock_client_cls):
	"""未包装的 ``database is locked`` 同样不能被兜成可重试的 NETWORK_ERROR。"""
	import sqlite3

	platform = _ctx_mock(mock_client_cls)
	platform.user_info.side_effect = sqlite3.OperationalError("database is locked")
	result = CliRunner().invoke(cli, ["--json", "me"])
	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "CACHE_BUSY"
	assert parsed["error"]["recoverable"] is True


@patch("boss_agent_cli.commands.me.get_platform_instance")
@patch("boss_agent_cli.commands.me.AuthManager")
def test_me_cache_database_invalid_points_at_backup_and_delete(mock_auth_cls, mock_client_cls, tmp_path):
	"""坏库给「备份后删库」指引，不能让人反复重试平台。"""
	from boss_agent_cli.cache.store import CacheDatabaseInvalid

	db_path = tmp_path / "cache" / "boss_agent.db"
	platform = _ctx_mock(mock_client_cls)
	platform.user_info.side_effect = CacheDatabaseInvalid(db_path, "database disk image is malformed")
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "me"])
	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "CACHE_DATABASE_INVALID"
	assert parsed["error"]["recoverable"] is True
	assert "备份" in parsed["error"]["recovery_action"]
	assert str(db_path) in parsed["hints"]["operator_actions"][0]


@patch("boss_agent_cli.commands.me.get_platform_instance")
@patch("boss_agent_cli.commands.me.AuthManager")
def test_me_sqlite_database_error_is_non_retryable_local_io_error(mock_auth_cls, mock_client_cls):
	"""裸 sqlite3.DatabaseError 是确定性本地失败，不能标成可重试网络错误。"""
	import sqlite3

	platform = _ctx_mock(mock_client_cls)
	platform.user_info.side_effect = sqlite3.DatabaseError("database disk image is malformed")
	result = CliRunner().invoke(cli, ["--json", "me"])
	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "LOCAL_IO_ERROR"
	assert parsed["error"]["recoverable"] is False
	assert "本地数据库文件无法打开" in parsed["error"]["message"]


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


@patch("boss_agent_cli.commands.me.get_platform_instance")
@patch("boss_agent_cli.commands.me.AuthManager")
def test_me_browser_kernel_missing_is_not_retryable_network_error(mock_auth_cls, mock_client_cls, tmp_path):
	"""`me` 自己的异常兜底也要识别内核缺失（同类缺陷的第三处）。

	me 没有 @handle_auth_errors 包裹，异常在命令体里逐个 except；只修装饰器
	会漏掉这条路径，Agent 从 me 拿到的仍会是「重试」。恢复指引必须指向
	patchright 安装，且指向本次数据目录的 doctor。
	"""
	mock_client_cls.side_effect = RuntimeError(
		"BrowserType.launch: Executable doesn't exist at "
		"/Users/u/Library/Caches/ms-playwright/chromium_headless_shell-1248/"
		"chrome-headless-shell-mac-arm64/chrome-headless-shell"
	)
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "me"])

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "BROWSER_KERNEL_MISSING"
	assert parsed["error"]["recoverable"] is True
	from boss_agent_cli.services.browser_runtime import patchright_install_command

	install = patchright_install_command()
	assert parsed["error"]["recovery_action"] == install
	assert install in parsed["hints"]["next_actions"]
	assert str(tmp_path.resolve()) in parsed["hints"]["next_actions"][1]
	assert "chrome-headless-shell-mac-arm64" not in parsed["error"]["message"]
