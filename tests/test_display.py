"""Tests for display.py — TTY detection, renderers, auth error decorator."""

import io
import sys
from unittest.mock import patch, MagicMock

from rich.console import Console

from boss_agent_cli.display import (
	boss_command_for_ctx,
	is_json_mode,
	handle_output,
	handle_error_output,
	handle_auth_errors,
	login_action_for_ctx,
)


def _capture_display_console(monkeypatch):
	stream = io.StringIO()
	console = Console(file=stream, force_terminal=False, width=100)
	monkeypatch.setattr("boss_agent_cli.display.console", console)
	return stream


class TestIsJsonMode:
	def test_force_json_flag(self):
		ctx = MagicMock()
		ctx.obj = {"json_output": True}
		assert is_json_mode(ctx) is True

	def test_piped_stdout(self):
		ctx = MagicMock()
		ctx.obj = {"json_output": False}
		with patch.object(sys, "stdout") as mock_out:
			mock_out.isatty.return_value = False
			assert is_json_mode(ctx) is True

	def test_tty_no_flag(self):
		ctx = MagicMock()
		ctx.obj = {"json_output": False}
		with patch.object(sys, "stdout") as mock_out:
			mock_out.isatty.return_value = True
			assert is_json_mode(ctx) is False

	def test_none_ctx(self):
		# When ctx is None, should check stdout
		with patch.object(sys, "stdout") as mock_out:
			mock_out.isatty.return_value = False
			assert is_json_mode(None) is True


class TestHandleOutput:
	def test_json_mode_emits_json(self):
		ctx = MagicMock()
		ctx.obj = {"json_output": True}
		with patch("boss_agent_cli.display.emit_success") as mock_emit:
			handle_output(ctx, "test", {"key": "val"})
			mock_emit.assert_called_once_with("test", {"key": "val"}, pagination=None, hints=None)

	def test_tty_mode_calls_render(self):
		ctx = MagicMock()
		ctx.obj = {"json_output": False}
		render_fn = MagicMock()
		with patch.object(sys, "stdout") as mock_out:
			mock_out.isatty.return_value = True
			handle_output(ctx, "test", {"key": "val"}, render=render_fn)
			render_fn.assert_called_once_with({"key": "val"})


class TestHandleErrorOutput:
	def test_json_mode_emits_error(self):
		ctx = MagicMock()
		ctx.obj = {"json_output": True}
		with patch("boss_agent_cli.output.emit_error") as mock_emit:
			handle_error_output(ctx, "test", code="ERR", message="bad")
			mock_emit.assert_called_once()


class TestHandleAuthErrors:
	def test_auth_required(self):
		from boss_agent_cli.auth.manager import AuthRequired
		ctx = MagicMock()
		ctx.obj = {"json_output": True}

		@handle_auth_errors("search")
		def impl(ctx):
			raise AuthRequired()

		with patch("boss_agent_cli.display.handle_error_output") as mock_err:
			impl(ctx)
			mock_err.assert_called_once()
			call_kwargs = mock_err.call_args
			assert call_kwargs[1]["code"] == "AUTH_REQUIRED"
			assert call_kwargs[1]["recovery_action"] == "boss login"

	def test_token_refresh_failed(self):
		from boss_agent_cli.auth.manager import TokenRefreshFailed
		ctx = MagicMock()
		ctx.obj = {"json_output": True}

		@handle_auth_errors("status")
		def impl(ctx):
			raise TokenRefreshFailed()

		with patch("boss_agent_cli.display.handle_error_output") as mock_err:
			impl(ctx)
			mock_err.assert_called_once()
			call_kwargs = mock_err.call_args
			assert call_kwargs[1]["code"] == "TOKEN_REFRESH_FAILED"
			assert call_kwargs[1]["recovery_action"] == "boss login"

	def test_auth_required_uses_zhilian_login_action(self):
		from boss_agent_cli.auth.manager import AuthRequired
		ctx = MagicMock()
		ctx.obj = {"json_output": True, "platform": "zhilian"}

		@handle_auth_errors("search")
		def impl(ctx):
			raise AuthRequired()

		with patch("boss_agent_cli.display.handle_error_output") as mock_err:
			impl(ctx)
			mock_err.assert_called_once()
			call_kwargs = mock_err.call_args
			assert call_kwargs[1]["recovery_action"] == "boss --platform zhilian login"

	def test_generic_exception(self):
		ctx = MagicMock()
		ctx.obj = {"json_output": True}

		@handle_auth_errors("me")
		def impl(ctx):
			raise ValueError("oops")

		with patch("boss_agent_cli.display.handle_error_output") as mock_err:
			impl(ctx)
			mock_err.assert_called_once()
			call_kwargs = mock_err.call_args
			assert call_kwargs[1]["code"] == "NETWORK_ERROR"

	def test_success_passthrough(self):
		ctx = MagicMock()
		ctx.obj = {"json_output": True}

		@handle_auth_errors("cities")
		def impl(ctx):
			return "ok"

		result = impl(ctx)
		assert result == "ok"


# ── handle_output 的 fallback 分支（TTY 但无 render） ──────────


class TestHandleOutputFallback:
	def test_tty_mode_without_render_emits_json(self):
		ctx = MagicMock()
		ctx.obj = {"json_output": False}
		with patch.object(sys, "stdout") as mock_out, \
			patch("boss_agent_cli.display.emit_success") as mock_emit:
			mock_out.isatty.return_value = True
			handle_output(ctx, "test", {"key": "val"})
			mock_emit.assert_called_once()


class TestLoginActionForCtx:
	def test_default_platform_uses_plain_boss_command(self):
		ctx = MagicMock()
		ctx.obj = {}
		assert boss_command_for_ctx(ctx, "status") == "boss status"

	def test_zhilian_platform_uses_platform_specific_boss_command(self):
		ctx = MagicMock()
		ctx.obj = {"platform": "zhilian"}
		assert boss_command_for_ctx(ctx, "status") == "boss --platform zhilian status"

	def test_default_platform_uses_plain_login(self):
		ctx = MagicMock()
		ctx.obj = {}
		assert login_action_for_ctx(ctx) == "boss login"

	def test_zhilian_platform_uses_platform_specific_login(self):
		ctx = MagicMock()
		ctx.obj = {"platform": "zhilian"}
		assert login_action_for_ctx(ctx) == "boss --platform zhilian login"

	def test_non_default_platform_uses_platform_specific_boss_command(self):
		ctx = MagicMock()
		ctx.obj = {"platform": "qiancheng"}
		assert boss_command_for_ctx(ctx, "status") == "boss --platform qiancheng status"
		assert login_action_for_ctx(ctx) == "boss --platform qiancheng login"


# ── handle_error_output TTY 分支 ─────────────────────────────


class TestHandleErrorOutputTTY:
	def test_tty_mode_raises_system_exit(self):
		import pytest
		ctx = MagicMock()
		ctx.obj = {"json_output": False}
		with patch.object(sys, "stdout") as mock_out:
			mock_out.isatty.return_value = True
			with pytest.raises(SystemExit):
				handle_error_output(
					ctx, "test", code="ERR", message="bad",
					recovery_action="try fix",
				)

	def test_tty_mode_without_recovery(self):
		import pytest
		ctx = MagicMock()
		ctx.obj = {"json_output": False}
		with patch.object(sys, "stdout") as mock_out:
			mock_out.isatty.return_value = True
			with pytest.raises(SystemExit):
				handle_error_output(ctx, "test", code="ERR", message="bad")


# ── handle_auth_errors AccountRiskError 分支 ─────────────────


class TestHandleAuthErrorsAccountRisk:
	def test_account_risk_non_cdp_stops_automation(self):
		from boss_agent_cli.api.client import AccountRiskError
		ctx = MagicMock()
		ctx.obj = {"json_output": True}

		@handle_auth_errors("search")
		def impl(ctx):
			raise AccountRiskError("风控", is_cdp=False)

		with patch("boss_agent_cli.display.handle_error_output") as mock_err:
			impl(ctx)
			mock_err.assert_called_once()
			kwargs = mock_err.call_args[1]
			assert kwargs["code"] == "ACCOUNT_RISK"
			assert kwargs["recoverable"] is False
			assert "停止自动化访问" in kwargs["recovery_action"]

	def test_account_risk_cdp_mode_suggests_contact_support(self):
		from boss_agent_cli.api.client import AccountRiskError
		ctx = MagicMock()
		ctx.obj = {"json_output": True}

		@handle_auth_errors("search")
		def impl(ctx):
			raise AccountRiskError("风控", is_cdp=True)

		with patch("boss_agent_cli.display.handle_error_output") as mock_err:
			impl(ctx)
			kwargs = mock_err.call_args[1]
			assert kwargs["recoverable"] is False
			assert "客服" in kwargs["recovery_action"]


# ── 各 renderer 冒烟测试（调用不抛异常即可覆盖） ────────────


class TestRenderers:
	def test_render_job_table_empty(self, monkeypatch):
		from boss_agent_cli.display import render_job_table
		stream = _capture_display_console(monkeypatch)

		render_job_table([], title="jobs")

		assert "no results" in stream.getvalue()

	def test_render_job_table_with_items(self, monkeypatch):
		from boss_agent_cli.display import render_job_table
		stream = _capture_display_console(monkeypatch)

		render_job_table(
			[
				{"title": "Go", "company": "X", "salary": "20K", "experience": "3-5年", "education": "本科", "city": "北京"},
				{"jobName": "Python", "brandName": "Y", "salaryDesc": "30K", "jobExperience": "5-10年", "jobDegree": "硕士", "cityName": "上海"},
			],
			title="jobs",
			page=1,
			hint_next="next hint",
		)

		output = stream.getvalue()
		assert "jobs (2 results)" in output
		# 第二行走的是 jobName/brandName/salaryDesc 这套平台原始键的回退分支
		assert "Go" in output and "Python" in output
		assert "20K" in output and "30K" in output
		assert "boss show" in output
		assert "next hint" in output

	def test_render_job_table_shows_internship_type(self, monkeypatch):
		from boss_agent_cli.display import render_job_table
		stream = _capture_display_console(monkeypatch)

		render_job_table(
			[{
				"title": "产品实习生",
				"company": "测试公司",
				"salary": "150元/天",
				"employment_type": "实习",
				"experience": "在校/应届",
				"education": "本科",
				"city": "杭州",
				"district": "滨江区",
			}],
			title="jobs",
		)

		output = stream.getvalue()
		assert "实习" in output

	def test_render_job_detail_minimal(self, monkeypatch):
		from boss_agent_cli.display import render_job_detail
		stream = _capture_display_console(monkeypatch)

		render_job_detail({"title": "Go", "salary": "20K"})

		output = stream.getvalue()
		assert "job detail" in output
		assert "Go" in output
		assert "20K" in output
		# 缺失字段回退成 "-"，而不是抛错或渲染出 None
		assert "None" not in output

	def test_render_job_detail_without_ids_omits_next_hint(self, monkeypatch):
		from boss_agent_cli.display import render_job_detail
		stream = _capture_display_console(monkeypatch)

		render_job_detail({"title": "Go", "salary": "20K"})

		assert "next:" not in stream.getvalue()

	def test_render_job_detail_exposes_available_greet_action(self, monkeypatch):
		"""有 security_id + job_id 时给出可执行的 CLI 下一步。"""
		from boss_agent_cli.display import render_job_detail
		stream = _capture_display_console(monkeypatch)

		render_job_detail({"title": "Go", "salary": "20K", "security_id": "sec1", "job_id": "job1"})

		output = stream.getvalue()
		assert "next:" in output
		assert "boss greet <security_id> <job_id>" in output

	def test_render_job_detail_truncates_long_description_at_500_chars(self, monkeypatch):
		from boss_agent_cli.display import render_job_detail
		stream = _capture_display_console(monkeypatch)

		# 用 z 作填充符：面板里其余文案（job detail / exp / edu / skills / company /
		# boss / description）都不含小写 z，所以计数就等于描述被保留的长度。
		render_job_detail({
			"title": "Go", "salary": "20K",
			"company": "X", "boss_name": "Z", "boss_title": "HR",
			"skills": ["Go", "Kafka"],
			"description": "z" * 800,
			"security_id": "sec1", "job_id": "job1",
		})

		output = stream.getvalue()
		assert output.count("z") == 500, "描述应被截断到 500 字符"
		assert "..." in output
		assert "description:" in output

	def test_render_status_logged_in(self, monkeypatch):
		from boss_agent_cli.display import render_status
		stream = _capture_display_console(monkeypatch)

		render_status({"logged_in": True, "user_name": "张三"})

		output = stream.getvalue()
		assert "logged in" in output
		assert "张三" in output
		assert "not logged in" not in output

	def test_render_status_not_logged_in(self, monkeypatch):
		from boss_agent_cli.display import render_status
		stream = _capture_display_console(monkeypatch)

		render_status({"logged_in": False})

		output = stream.getvalue()
		assert "not logged in" in output
		assert "boss login" in output

	def test_render_status_not_logged_in_with_custom_login_action(self, monkeypatch):
		from boss_agent_cli.display import render_status
		stream = _capture_display_console(monkeypatch)

		render_status({"logged_in": False}, login_action="boss --platform zhilian login")

		output = stream.getvalue()
		assert "not logged in" in output
		assert "boss --platform zhilian login" in output

	def test_render_simple_list_empty(self, monkeypatch):
		from boss_agent_cli.display import render_simple_list
		stream = _capture_display_console(monkeypatch)

		render_simple_list([], title="items", columns=[("name", "name", "cyan")])

		assert "no items" in stream.getvalue()

	def test_render_simple_list_with_items(self, monkeypatch):
		from boss_agent_cli.display import render_simple_list
		stream = _capture_display_console(monkeypatch)

		render_simple_list(
			[{"name": "A", "stage": "s1"}, {"name": "B", "stage": "s2"}],
			title="items",
			columns=[("Name", "name", "cyan"), ("Stage", "stage", "green")],
		)

		output = stream.getvalue()
		assert "items (2)" in output
		assert "Name" in output and "Stage" in output
		assert "s1" in output and "s2" in output

	def test_render_simple_list_falls_back_for_missing_keys(self, monkeypatch):
		from boss_agent_cli.display import render_simple_list
		stream = _capture_display_console(monkeypatch)

		render_simple_list(
			[{"name": "A"}],
			title="items",
			columns=[("Name", "name", "cyan"), ("Stage", "stage", "green")],
		)

		output = stream.getvalue()
		assert "-" in output
		assert "None" not in output, "缺字段应渲染为 -，不能把 None 显示给用户"

	def test_render_message_panel(self, monkeypatch):
		from boss_agent_cli.display import render_message_panel
		stream = _capture_display_console(monkeypatch)

		render_message_panel({"a": 1, "b": "x"}, title="result")

		output = stream.getvalue()
		assert "result" in output
		assert "a:" in output and "b:" in output
		assert "1" in output and "x" in output

	def test_render_batch_operation_summary_dry_run_with_candidates(self, monkeypatch):
		from boss_agent_cli.display import render_batch_operation_summary
		stream = _capture_display_console(monkeypatch)

		render_batch_operation_summary({
			"dry_run": True,
			"candidates": [{"title": "Go", "company": "X", "salary": "20K", "experience": "3年", "education": "本科", "city": "北京"}],
		})

		output = stream.getvalue()
		assert "dry run" in output
		assert "1 candidates" in output
		assert "Go" in output
		assert "success:" not in output, "dry run 不得出现执行结果计数"

	def test_render_batch_operation_summary_dry_run_empty(self, monkeypatch):
		from boss_agent_cli.display import render_batch_operation_summary
		stream = _capture_display_console(monkeypatch)

		render_batch_operation_summary({"dry_run": True, "candidates": []})

		output = stream.getvalue()
		assert "dry run" in output
		assert "0 candidates" in output

	def test_render_batch_operation_summary_success(self, monkeypatch):
		from boss_agent_cli.display import render_batch_operation_summary
		stream = _capture_display_console(monkeypatch)

		render_batch_operation_summary({
			"dry_run": False,
			"greeted": [{"title": "Go", "company": "X"}],
			"failed": [{"title": "Python", "company": "Y"}],
			"stopped_reason": "rate limited",
		})

		output = stream.getvalue()
		assert "success: 1" in output
		assert "failed: 1" in output
		assert "rate limited" in output

	def test_render_sectioned_record_mixed(self, monkeypatch):
		from boss_agent_cli.display import render_sectioned_record
		stream = _capture_display_console(monkeypatch)

		render_sectioned_record({
			"info": {"name": "张三", "phone": "13800000000", "tags": ["A", "B"], "meta": {"k": "v"}},
			"expect": {},
			"note": "一句话说明",
		})

		output = stream.getvalue()
		assert "info" in output and "张三" in output
		assert "empty" in output, "空 section 应显式渲染 empty 而不是留白"
		assert "一句话说明" in output, "非 dict 的 section 走裸行渲染"

	def test_render_sectioned_record_truncates_nested_values(self, monkeypatch):
		from boss_agent_cli.display import render_sectioned_record
		stream = _capture_display_console(monkeypatch)

		render_sectioned_record({"info": {"blob": ["z" * 400]}})

		# 嵌套 list/dict 被 str() 后截到 200 字符，避免一条记录刷屏
		assert stream.getvalue().count("z") == 198

	def test_render_string_grid_empty(self, monkeypatch):
		from boss_agent_cli.display import render_string_grid
		stream = _capture_display_console(monkeypatch)

		render_string_grid([], title="cities")

		assert "no cities" in stream.getvalue()

	def test_render_string_grid_with_items(self, monkeypatch):
		from boss_agent_cli.display import render_string_grid
		stream = _capture_display_console(monkeypatch)

		render_string_grid(["北京", "上海", "广州", "深圳", "杭州"], title="cities", columns=4)

		output = stream.getvalue()
		assert "cities (5)" in output
		for city in ("北京", "上海", "广州", "深圳", "杭州"):
			assert city in output, f"{city} 应出现在网格里"

	def test_render_export_summary_with_path(self, monkeypatch):
		from boss_agent_cli.display import render_export_summary
		stream = _capture_display_console(monkeypatch)

		render_export_summary({"path": "/tmp/x.csv", "count": 20, "format": "csv"})

		output = stream.getvalue()
		assert "exported 20 jobs" in output
		assert "/tmp/x.csv" in output
		assert "csv" in output

	def test_render_export_summary_without_path(self, monkeypatch):
		from boss_agent_cli.display import render_export_summary
		stream = _capture_display_console(monkeypatch)

		render_export_summary({"count": 5, "format": "json"})

		output = stream.getvalue()
		assert "exported 5 jobs" in output
		assert "json" in output
		assert " to " not in output, "无 path 时不应出现 'to <路径>' 片段"


# ── operator_actions 双受众渲染（TTY 分支）─────────────────


class TestRenderOperatorActions:
	"""render_operator_actions 只渲染面向真人的通道，且只走 stderr。"""

	def _tty_ctx(self):
		ctx = MagicMock()
		ctx.obj = {"json_output": False}
		return ctx

	def test_operator_actions_rendered_in_tty(self, monkeypatch):
		stream = _capture_display_console(monkeypatch)
		rendered = []
		with patch.object(sys, "stdout") as mock_out:
			mock_out.isatty.return_value = True
			handle_output(
				self._tty_ctx(),
				"wizard",
				{"ok": True},
				render=rendered.append,
				hints={"operator_actions": ["扫码登录后回到终端"]},
			)
		assert rendered == [{"ok": True}]
		assert "你需要" in stream.getvalue()
		assert "扫码登录后回到终端" in stream.getvalue()

	def test_next_actions_not_rendered_in_tty(self, monkeypatch):
		"""A4：next_actions 是纯 Agent 通道，TTY 下不渲染。"""
		stream = _capture_display_console(monkeypatch)
		with patch.object(sys, "stdout") as mock_out:
			mock_out.isatty.return_value = True
			handle_output(
				self._tty_ctx(),
				"search",
				{"items": []},
				render=lambda _data: None,
				hints={"next_actions": ["boss show 1", "boss shortlist add x y"]},
			)
		assert stream.getvalue() == ""

	def test_no_hints_produces_no_output(self, monkeypatch):
		"""A5：无 hints 的命令 TTY 输出零变化。"""
		stream = _capture_display_console(monkeypatch)
		with patch.object(sys, "stdout") as mock_out:
			mock_out.isatty.return_value = True
			handle_output(
				self._tty_ctx(), "search", {"items": []}, render=lambda _data: None
			)
		assert stream.getvalue() == ""

	def test_empty_operator_actions_produces_no_output(self, monkeypatch):
		stream = _capture_display_console(monkeypatch)
		with patch.object(sys, "stdout") as mock_out:
			mock_out.isatty.return_value = True
			for hints in ({}, {"operator_actions": []}, {"operator_actions": None}):
				handle_output(
					self._tty_ctx(),
					"search",
					{},
					render=lambda _data: None,
					hints=hints,
				)
		assert stream.getvalue() == ""

	def test_multiple_actions_align_continuation_lines(self, monkeypatch):
		stream = _capture_display_console(monkeypatch)
		with patch.object(sys, "stdout") as mock_out:
			mock_out.isatty.return_value = True
			handle_output(
				self._tty_ctx(),
				"wizard",
				{},
				render=lambda _data: None,
				hints={"operator_actions": ["第一步动作", "第二步动作"]},
			)
		output = stream.getvalue()
		assert "↓ 你需要：第一步动作" in output
		assert "第二步动作" in output
		assert output.count("你需要") == 1

	def test_json_mode_does_not_render(self, monkeypatch):
		"""JSON 模式走信封，不触发 Rich 渲染。"""
		stream = _capture_display_console(monkeypatch)
		ctx = MagicMock()
		ctx.obj = {"json_output": True}
		with patch("boss_agent_cli.display.emit_success") as mock_emit:
			handle_output(
				ctx,
				"wizard",
				{},
				render=lambda _data: None,
				hints={"operator_actions": ["扫码登录"]},
			)
			mock_emit.assert_called_once()
		assert stream.getvalue() == ""

	def test_error_output_renders_operator_actions_before_exit(self, monkeypatch):
		import pytest

		stream = _capture_display_console(monkeypatch)
		with patch.object(sys, "stdout") as mock_out:
			mock_out.isatty.return_value = True
			with pytest.raises(SystemExit):
				handle_error_output(
					self._tty_ctx(),
					"login",
					code="LOGIN_TIMEOUT",
					message="登录等待超时",
					recovery_action="boss login",
					hints={
						"next_actions": ["boss login --timeout 180"],
						"operator_actions": ["确认二维码已完成扫码"],
					},
				)
		output = stream.getvalue()
		assert "确认二维码已完成扫码" in output
		assert "boss login --timeout 180" not in output


# ── 真人链路：下一步提示与动作确认渲染 ──────────────────────────


class TestRenderNextSteps:
	def test_renders_each_action(self, monkeypatch):
		from boss_agent_cli.display import render_next_steps

		stream = _capture_display_console(monkeypatch)
		render_next_steps(["boss resume show x", "boss resume list"])
		out = stream.getvalue()

		assert "boss resume show x" in out
		assert "boss resume list" in out
		assert "下一步" in out

	def test_empty_actions_render_nothing(self, monkeypatch):
		from boss_agent_cli.display import render_next_steps

		stream = _capture_display_console(monkeypatch)
		render_next_steps([])

		assert stream.getvalue() == ""

	def test_none_renders_nothing(self, monkeypatch):
		from boss_agent_cli.display import render_next_steps

		stream = _capture_display_console(monkeypatch)
		render_next_steps(None)

		assert stream.getvalue() == ""


class TestRenderActionResult:
	def test_shows_fields_and_next_steps(self, monkeypatch):
		from boss_agent_cli.display import render_action_result

		stream = _capture_display_console(monkeypatch)
		render_action_result(
			{"action": "init", "name": "我的简历", "template": "default"},
			title="resume",
			next_steps=["boss resume show 我的简历"],
		)
		out = stream.getvalue()

		assert "init" in out
		assert "我的简历" in out
		assert "boss resume show" in out
		assert "{" not in out, "不应回显 JSON"

	def test_works_without_next_steps(self, monkeypatch):
		from boss_agent_cli.display import render_action_result

		stream = _capture_display_console(monkeypatch)
		render_action_result({"action": "remove", "name": "x", "removed": True}, title="preset")
		out = stream.getvalue()

		assert "remove" in out
		assert "下一步" not in out


class TestRenderListWithSteps:
	def test_empty_list_still_gives_next_step(self, monkeypatch):
		"""空列表必须给出可执行的下一步，而不是只说 no xxx（AC4）。"""
		from boss_agent_cli.display import render_list_result

		stream = _capture_display_console(monkeypatch)
		render_list_result([], "resumes", [("name", "name", "cyan")], next_steps=["boss resume init"])
		out = stream.getvalue()

		assert "boss resume init" in out

	def test_non_empty_list_renders_rows(self, monkeypatch):
		from boss_agent_cli.display import render_list_result

		stream = _capture_display_console(monkeypatch)
		render_list_result(
			[{"name": "简历A"}, {"name": "简历B"}],
			"resumes",
			[("name", "name", "cyan")],
			next_steps=[],
		)
		out = stream.getvalue()

		assert "简历A" in out and "简历B" in out


class TestRenderAiResult:
	def test_renders_scalar_and_list_values(self, monkeypatch):
		from boss_agent_cli.display import render_ai_result

		stream = _capture_display_console(monkeypatch)
		render_ai_result(
			{"匹配度": "85分", "优势": ["Go 经验充足", "分布式背景"], "建议": {"简历": "补充量化指标"}},
			title="ai fit",
		)
		out = stream.getvalue()

		assert "85分" in out
		assert "Go 经验充足" in out
		assert "分布式背景" in out
		assert "补充量化指标" in out

	def test_does_not_truncate_long_text(self, monkeypatch):
		"""AI 长文本（润色后的简历等）不得被截断。"""
		from boss_agent_cli.display import render_ai_result

		unit = "补充项目量化指标。"
		long_text = "优化建议：" + unit * 40
		stream = _capture_display_console(monkeypatch)
		render_ai_result({"内容": long_text}, title="ai polish")
		# Panel 会给每行加 │ 边框并按宽度换行，比对前先去掉边框与空白
		out = stream.getvalue().replace("│", "").replace("\n", "").replace(" ", "")

		assert out.count(unit) == 40, f"长文本被截断，仅剩 {out.count(unit)} 段"

	def test_escapes_rich_markup_from_ai_output(self, monkeypatch):
		"""AI 输出里的方括号不得被当成 Rich 标记解析。"""
		from boss_agent_cli.display import render_ai_result

		stream = _capture_display_console(monkeypatch)
		render_ai_result({"建议": "把 [bold]重点[/bold] 写在前面"}, title="ai suggest")
		out = stream.getvalue()

		assert "[bold]" in out, "方括号应原样显示而不是被解析成样式"

	def test_renders_next_steps(self, monkeypatch):
		from boss_agent_cli.display import render_ai_result

		stream = _capture_display_console(monkeypatch)
		render_ai_result({"x": "y"}, title="ai", next_steps=["boss ai optimize <name>"])

		assert "boss ai optimize" in stream.getvalue()


class TestSearchProgress:
	def _progress(self, monkeypatch, **kw):
		from boss_agent_cli.display import SearchProgress

		stream = _capture_display_console(monkeypatch)
		return SearchProgress("搜索 Golang", max_pages=3, **kw), stream

	def test_duck_types_logger_interface(self, monkeypatch):
		progress, _ = self._progress(monkeypatch)
		for method in ("info", "debug", "warning", "error"):
			assert callable(getattr(progress, method))

	def test_counts_pages_and_matches(self, monkeypatch):
		progress, _ = self._progress(monkeypatch)
		progress.info("正在搜索第 1 页...")
		progress.info("  ✅ 字节跳动 - Golang 后端（详情匹配）")
		progress.info("  ✅ 腾讯 - 后端开发（标签匹配）")
		progress.info("  ❌ 某某科技 - Go 工程师")
		progress.info("  预筛排除: 前端开发 (岗位类型不符)")

		assert progress.pages == 1
		assert progress.matched == 2
		assert progress.excluded == 2

	def test_prints_matched_lines_but_not_excluded_spam(self, monkeypatch):
		progress, stream = self._progress(monkeypatch)
		progress.info("  ✅ 字节跳动 - Golang 后端（详情匹配）")
		progress.info("  ❌ 某某科技 - Go 工程师")
		out = stream.getvalue()

		assert "字节跳动" in out, "匹配结果应逐条可见"
		assert "某某科技" not in out, "逐条排除不应刷屏"

	def test_status_line_reports_counters(self, monkeypatch):
		progress, _ = self._progress(monkeypatch)
		progress.info("正在搜索第 1 页...")
		progress.info("  ✅ A - B（详情匹配）")

		status = progress.status_text()
		assert "1/3" in status
		assert "匹配 1" in status

	def test_thread_safe_counting(self, monkeypatch):
		import threading

		progress, _ = self._progress(monkeypatch)

		def worker():
			for _ in range(50):
				progress.info("  ✅ A - B（详情匹配）")

		threads = [threading.Thread(target=worker) for _ in range(4)]
		for t in threads:
			t.start()
		for t in threads:
			t.join()

		assert progress.matched == 200

	def test_debug_is_not_rendered(self, monkeypatch):
		progress, stream = self._progress(monkeypatch)
		progress.debug("搜索命中缓存")

		assert stream.getvalue() == ""


def test_search_pipeline_progress_markers_contract():
	"""契约锁定：SearchProgress 依赖管线里的这些标记来分类进度消息。

	改动 search_filters.py 的进度文案会让 TTY 进度静默退化，
	因此在这里显式锁定——格式变了就让这条测试大声失败。
	"""
	from pathlib import Path

	source = Path(__file__).resolve().parents[1] / "src/boss_agent_cli/search_filters.py"
	content = source.read_text(encoding="utf-8")

	assert '"正在搜索第 {current_page} 页..."' in content.replace("f\"", "\"")
	assert "✅ {company} - {title}（详情匹配）" in content
	assert "❌ {company} - {title}" in content
	assert "预筛排除:" in content
