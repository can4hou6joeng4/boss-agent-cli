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

	def test_render_job_detail_hands_sensitive_action_back_to_the_platform(self, monkeypatch):
		"""有 security_id + job_id 时给出的默认引导必须是「回官网手动完成」。"""
		from boss_agent_cli.display import render_job_detail
		stream = _capture_display_console(monkeypatch)

		render_job_detail({"title": "Go", "salary": "20K", "security_id": "sec1", "job_id": "job1"})

		output = stream.getvalue()
		assert "next:" in output
		assert "回到平台官网" in output

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
