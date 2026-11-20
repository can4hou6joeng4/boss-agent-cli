"""第十三轮审计批次：Issue #2245–#2247 的回归测试。

- #2245：``escape_markup`` 剥离 ESC / OSC / CSI 等 C0 / C1 控制字符，
  Rich 的 ``escape`` 只挡 markup，挡不住原始转义字节。
- #2246：错误行的 ``code`` 按字面转义，未注册错误码不再让 TTY 输出抛
  MarkupError。
- #2247：TTY 渲染入口跳过非对象条目，``skills`` 形状漂移不再抛 TypeError。

全部测试离线运行；只检查本地渲染输出，不调用平台 API。
"""

from __future__ import annotations

import io
from types import SimpleNamespace
from typing import Any

import pytest
from rich.console import Console

from boss_agent_cli import display
from boss_agent_cli.display import (
	escape_markup,
	render_job_detail,
	render_job_table,
	render_simple_list,
)


@pytest.fixture
def rendered(monkeypatch) -> io.StringIO:
	stream = io.StringIO()
	monkeypatch.setattr(display, "console", Console(file=stream, force_terminal=False, width=200))
	return stream


@pytest.fixture
def tty_errors(rendered, monkeypatch) -> io.StringIO:
	# 强制走 TTY 分支：管道下错误走 JSON 信封，不经过这里的 Rich 渲染。
	monkeypatch.setattr(display, "is_json_mode", lambda _ctx: False)
	return rendered


# ── #2245：控制字符剥离 ────────────────────────────────────────────


def test_escape_markup_strips_terminal_control_sequences() -> None:
	payload = "职位\x1b]0;pwn\x07名\x1b[2J\x9b31m\x7f"
	out = escape_markup(payload)

	assert "\x1b" not in out
	assert "\x07" not in out
	assert "\x9b" not in out
	assert "\x7f" not in out
	assert "职位" in out and "名" in out


def test_escape_markup_keeps_newlines_and_tabs() -> None:
	# 描述类字段需要多行渲染，只剥离真正的终端控制字符。
	assert escape_markup("第一行\n第二行\t尾部") == "第一行\n第二行\t尾部"


def test_escape_markup_still_escapes_markup() -> None:
	assert escape_markup("[/bold]") == "[none]\\[/bold][/none]"


def test_render_job_table_strips_control_chars(rendered) -> None:
	render_job_table([{"title": "工程师\x1b]0;pwn\x07"}], "推荐职位")

	out = rendered.getvalue()
	assert "\x1b" not in out
	assert "\x07" not in out
	assert "工程师" in out


# ── #2246：错误码按字面渲染 ───────────────────────────────────────


def test_error_line_escapes_unregistered_code_markup(tty_errors) -> None:
	with pytest.raises(SystemExit) as excinfo:
		display.handle_error_output(
			SimpleNamespace(obj={}), "search", code="X[bold", message="boom", hints=None
		)

	assert excinfo.value.code == 1
	out = tty_errors.getvalue()
	assert "X[bold" in out
	assert "boom" in out


def test_error_line_strips_control_chars_from_message(tty_errors) -> None:
	with pytest.raises(SystemExit):
		display.handle_error_output(
			SimpleNamespace(obj={}), "search", code="NETWORK_ERROR", message="a\x1b[2Jb", hints=None
		)

	out = tty_errors.getvalue()
	assert "\x1b" not in out
	assert "a" in out and "b" in out


# ── #2247：渲染入口类型漂移 ───────────────────────────────────────


def test_render_job_table_skips_non_object_items(rendered) -> None:
	items: list[Any] = [None, "abc", 5, {"title": "工程师", "company": "ACME"}]
	render_job_table(items, "推荐职位")

	out = rendered.getvalue()
	assert "工程师" in out
	assert "推荐职位 (1 results)" in out


def test_render_simple_list_skips_non_object_items(rendered) -> None:
	items: list[Any] = ["abc", None, {"name": "张三"}]
	render_simple_list(items, "联系人", [("姓名", "name", "")])

	out = rendered.getvalue()
	assert "张三" in out
	assert "联系人 (1)" in out


@pytest.mark.parametrize("skills", [5, None, {"a": 1}, 3.5])
def test_render_job_detail_tolerates_non_sequence_skills(rendered, skills) -> None:
	render_job_detail({"title": "工程师", "skills": skills})

	assert "skills: -" in rendered.getvalue()


def test_render_job_detail_treats_string_skills_as_single_entry(rendered) -> None:
	render_job_detail({"title": "工程师", "skills": "Python"})

	out = rendered.getvalue()
	assert "skills: Python" in out
	assert "P, y" not in out
