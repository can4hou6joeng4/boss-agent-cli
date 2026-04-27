"""Tests for display.py — TTY detection, renderers, auth error decorator."""

import sys
from unittest.mock import patch, MagicMock

from boss_agent_cli.display import (
	boss_command_for_ctx,
	is_json_mode,
	handle_output,
	handle_error_output,
	handle_auth_errors,
	login_action_for_ctx,
)


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
	def test_account_risk_non_cdp_suggests_cdp_chrome(self):
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
			assert kwargs["recoverable"] is True
			assert "9222" in kwargs["recovery_action"]

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
	def test_render_job_table_empty(self):
		from boss_agent_cli.display import render_job_table
		render_job_table([], title="jobs")

	def test_render_job_table_with_items(self):
		from boss_agent_cli.display import render_job_table
		render_job_table(
			[
				{"title": "Go", "company": "X", "salary": "20K", "experience": "3-5年", "education": "本科", "city": "北京"},
				{"jobName": "Python", "brandName": "Y", "salaryDesc": "30K", "jobExperience": "5-10年", "jobDegree": "硕士", "cityName": "上海"},
			],
			title="jobs",
			page=1,
			hint_next="next hint",
		)

	def test_render_job_detail_minimal(self):
		from boss_agent_cli.display import render_job_detail
		render_job_detail({"title": "Go", "salary": "20K"})

	def test_render_job_detail_with_long_description_truncated(self):
		from boss_agent_cli.display import render_job_detail
		long_desc = "a" * 800
		render_job_detail({
			"title": "Go", "salary": "20K",
			"company": "X", "boss_name": "Z", "boss_title": "HR",
			"skills": ["Go", "Kafka"],
			"description": long_desc,
			"security_id": "sec1", "job_id": "job1",
		})

	def test_render_status_logged_in(self):
		from boss_agent_cli.display import render_status
		render_status({"logged_in": True, "user_name": "张三"})

	def test_render_status_not_logged_in(self):
		from boss_agent_cli.display import render_status
		render_status({"logged_in": False})

	def test_render_status_not_logged_in_with_custom_login_action(self):
		from boss_agent_cli.display import render_status
		render_status({"logged_in": False}, login_action="boss --platform zhilian login")

	def test_render_simple_list_empty(self):
		from boss_agent_cli.display import render_simple_list
		render_simple_list([], title="items", columns=[("name", "name", "cyan")])

	def test_render_simple_list_with_items(self):
		from boss_agent_cli.display import render_simple_list
		render_simple_list(
			[{"name": "A", "stage": "s1"}, {"name": "B", "stage": "s2"}],
			title="items",
			columns=[("Name", "name", "cyan"), ("Stage", "stage", "green")],
		)

	def test_render_message_panel(self):
		from boss_agent_cli.display import render_message_panel
		render_message_panel({"a": 1, "b": "x"}, title="result")

	def test_render_batch_operation_summary_dry_run_with_candidates(self):
		from boss_agent_cli.display import render_batch_operation_summary
		render_batch_operation_summary({
			"dry_run": True,
			"candidates": [{"title": "Go", "company": "X", "salary": "20K", "experience": "3年", "education": "本科", "city": "北京"}],
		})

	def test_render_batch_operation_summary_dry_run_empty(self):
		from boss_agent_cli.display import render_batch_operation_summary
		render_batch_operation_summary({"dry_run": True, "candidates": []})

	def test_render_batch_operation_summary_success(self):
		from boss_agent_cli.display import render_batch_operation_summary
		render_batch_operation_summary({
			"dry_run": False,
			"greeted": [{"title": "Go", "company": "X"}],
			"failed": [{"title": "Python", "company": "Y"}],
			"stopped_reason": "rate limited",
		})

	def test_render_sectioned_record_mixed(self):
		from boss_agent_cli.display import render_sectioned_record
		render_sectioned_record({
			"info": {"name": "张三", "phone": "13800000000", "tags": ["A", "B"], "meta": {"k": "v"}},
			"expect": {},
			"note": "一句话说明",
		})

	def test_render_string_grid_empty(self):
		from boss_agent_cli.display import render_string_grid
		render_string_grid([], title="cities")

	def test_render_string_grid_with_items(self):
		from boss_agent_cli.display import render_string_grid
		render_string_grid(["北京", "上海", "广州", "深圳", "杭州"], title="cities", columns=4)

	def test_render_export_summary_with_path(self):
		from boss_agent_cli.display import render_export_summary
		render_export_summary({"path": "/tmp/x.csv", "count": 20, "format": "csv"})

	def test_render_export_summary_without_path(self):
		from boss_agent_cli.display import render_export_summary
		render_export_summary({"count": 5, "format": "json"})
