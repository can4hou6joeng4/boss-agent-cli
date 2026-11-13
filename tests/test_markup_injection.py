"""平台外部字符串进入 Rich 前的字面量转义（Issue #953）。

公司名、职位名、职位描述、聊天内容与昵称都是平台外部输入。Rich 默认把字符串
按 markup 解析：未配对的闭合标签（``[/bold]``）会抛 MarkupError 让整条 TTY
输出崩掉，``[link=...]`` 则会把终端渲染成可点击超链接（OSC 8）。这里用
``force_terminal=True`` 的 Console 断言两件事都不再发生，且原文按字面显示。
"""

import io
import re
from pathlib import Path

import pytest
from rich.console import Console

_CLOSING_TAG = "看到 [/bold] 崩溃"
_LINK = "[link=https://example.invalid/x]点我领奖[/link]"
_PAYLOADS = (_CLOSING_TAG, _LINK)


def _capture_terminal_console(monkeypatch):
	"""force_terminal 才会走到 OSC 8 超链接编码；force_terminal=False 掩盖注入。"""
	stream = io.StringIO()
	console = Console(file=stream, force_terminal=True, width=120, color_system="truecolor")
	monkeypatch.setattr("boss_agent_cli.display.console", console)
	return stream


_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def _assert_literal(stream, payload: str) -> None:
	output = stream.getvalue()
	assert "\x1b]8;" not in output, "平台字符串不能被渲染成终端超链接"
	# 转义序列被 markup 解析消费掉，原文（含方括号）按字面出现；样式切换的
	# ANSI 序列会把文本切碎，断言前先剥掉。表格列宽会截断长值，链接 payload
	# 只用前缀作「没有被当作链接解析」的标记。
	plain = _ANSI_ESCAPE.sub("", output)
	marker = "[link=" if payload == _LINK else payload
	assert marker in plain


class TestDisplayRenderers:
	@pytest.mark.parametrize("payload", _PAYLOADS)
	def test_job_table(self, monkeypatch, payload):
		from boss_agent_cli.display import render_job_table

		stream = _capture_terminal_console(monkeypatch)
		render_job_table([{"title": payload, "company": payload, "salary": payload}], title="jobs")

		_assert_literal(stream, payload)

	@pytest.mark.parametrize("payload", _PAYLOADS)
	def test_job_detail(self, monkeypatch, payload):
		from boss_agent_cli.display import render_job_detail

		stream = _capture_terminal_console(monkeypatch)
		render_job_detail(
			{
				"title": payload,
				"company": payload,
				"boss_name": payload,
				"boss_title": payload,
				"description": payload,
				"skills": [payload],
			}
		)

		_assert_literal(stream, payload)

	@pytest.mark.parametrize("payload", _PAYLOADS)
	def test_simple_list(self, monkeypatch, payload):
		from boss_agent_cli.display import render_simple_list

		stream = _capture_terminal_console(monkeypatch)
		render_simple_list(
			[{"name": payload, "company": payload}],
			"contacts",
			[("name", "name", "bold"), ("company", "company", "dim")],
		)

		_assert_literal(stream, payload)

	@pytest.mark.parametrize("payload", _PAYLOADS)
	def test_message_panel(self, monkeypatch, payload):
		from boss_agent_cli.display import render_message_panel

		stream = _capture_terminal_console(monkeypatch)
		render_message_panel({"content": payload, "from": payload}, title="chatmsg")

		_assert_literal(stream, payload)

	@pytest.mark.parametrize("payload", _PAYLOADS)
	def test_sectioned_record(self, monkeypatch, payload):
		from boss_agent_cli.display import render_sectioned_record

		stream = _capture_terminal_console(monkeypatch)
		render_sectioned_record({"profile": {"name": payload}, "note": payload}, title="me")

		_assert_literal(stream, payload)

	@pytest.mark.parametrize("payload", _PAYLOADS)
	def test_status_user_name(self, monkeypatch, payload):
		from boss_agent_cli.display import render_status

		stream = _capture_terminal_console(monkeypatch)
		render_status({"logged_in": True, "user_name": payload})

		_assert_literal(stream, payload)


class TestWizardRenderers:
	@pytest.mark.parametrize("payload", _PAYLOADS)
	def test_job_card(self, monkeypatch, payload):
		from boss_agent_cli.wizard.renderer import render_job_card_zh

		stream = _capture_terminal_console(monkeypatch)
		render_job_card_zh(
			{
				"title": payload,
				"company": payload,
				"boss_name": payload,
				"description": payload,
				"address": payload,
				"skills": [payload],
			}
		)

		_assert_literal(stream, payload)

	@pytest.mark.parametrize("payload", _PAYLOADS)
	def test_crawl_job_brief(self, monkeypatch, payload):
		from boss_agent_cli.wizard.renderer import render_crawl_job_brief

		stream = _capture_terminal_console(monkeypatch)
		render_crawl_job_brief(
			{"title": payload, "company": payload, "description": payload, "labels": [payload]},
			with_description=True,
		)

		_assert_literal(stream, payload)

	@pytest.mark.parametrize("payload", _PAYLOADS)
	def test_wizard_error_panel(self, monkeypatch, payload):
		from boss_agent_cli.wizard.renderer import render_error

		stream = _capture_terminal_console(monkeypatch)
		render_error(code="NETWORK_ERROR", message=f"平台返回：{payload}", recovery_action=payload)

		_assert_literal(stream, payload)

	@pytest.mark.parametrize("payload", _PAYLOADS)
	def test_waiting_input_reason(self, monkeypatch, payload):
		from boss_agent_cli.wizard.renderer import render_run

		stream = _capture_terminal_console(monkeypatch)
		render_run(
			{
				"status": "waiting_input",
				"goal": "crawl_start",
				"role": "candidate",
				"last_result": {"data": {"error": payload}},
			}
		)

		_assert_literal(stream, payload)


class TestAiLocalRenderers:
	"""本地 AI 配置值（provider / model / base_url）也是自由文本（Issue #964）。"""

	@pytest.mark.parametrize("payload", _PAYLOADS)
	def test_local_status(self, monkeypatch, payload):
		import click

		from boss_agent_cli.commands import ai_local

		ctx = click.Context(click.Command("ai"))
		ctx.obj = {"data_dir": Path("/tmp/data"), "platform": "zhipin"}
		stream = _capture_terminal_console(monkeypatch)
		ai_local._render_local_status(
			ctx,
			{
				"configured": True,
				"provider": payload,
				"model": payload,
				"base_url": payload,
				"recommended_models": [
					{"name": payload, "runtime": payload, "min_memory_gb": 8, "description": payload},
				],
			},
		)

		_assert_literal(stream, payload)


class TestEscapeMarkupHelper:
	def test_returns_literal_text_for_rich_tags(self):
		from boss_agent_cli.display import escape_markup

		assert escape_markup("[bold]x[/bold]") == "\\[bold]x\\[/bold]"
		assert escape_markup(42) == "42"
