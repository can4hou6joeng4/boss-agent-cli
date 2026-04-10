"""Tests for display.py — TTY detection, renderers, auth error decorator."""

import sys
from unittest.mock import patch, MagicMock

from boss_agent_cli.display import (
	is_json_mode,
	handle_output,
	handle_error_output,
	handle_auth_errors,
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
