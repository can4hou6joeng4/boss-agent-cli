"""外部文本的终端渲染边界（#2251、#2252）；全部离线。"""

from __future__ import annotations

import io
import itertools
from typing import Any

import pytest
from rich.control import Control
from rich.text import Text

from boss_agent_cli import display
from boss_agent_cli.wizard import prompts, renderer


@pytest.mark.parametrize(
	"values",
	[
		("ACME[a", "北京]", ""),
		("王[/", "HRBP]", ""),
		("[link=https://example.invalid", "]Go", "[/link]"),
		("[bold]工程师[/bold]", "[/x]", "[1]"),
		("C:\\[1]", "a\\[b", "\\[bold]"),
		("\\\\[bold]", "\\\\\\[bold]", "\\\\[1]"),
		*(("a" + "\\" * count, "\\" * count, "[a") for count in range(1, 5)),
	],
)
def test_adjacent_values_preserve_text_and_surrounding_styles(values) -> None:
	first, second, third = values
	markup = (
		f"[bold]{display.escape_markup(first)}[/bold] · "
		f"{display.escape_markup(second)}（{display.escape_markup(third)}）[dim]尾[/dim]"
	)
	text = Text.from_markup(markup, emoji=False)
	expected = f"{first} · {second}（{third}）尾"
	assert text.plain == expected
	# 外部值不能吃掉调用方的闭合标签，bold 不得漏到后面的列。
	assert [(span.start, span.end) for span in text.spans if str(span.style) == "bold"] == [(0, len(first))]
	assert [(span.start, span.end) for span in text.spans if str(span.style) == "dim"] == [
		(len(expected) - 1, len(expected))
	]
	assert not any("link" in str(span.style) for span in text.spans)


def test_short_markup_fragments_remain_literal_in_three_positions() -> None:
	# 覆盖标签起始、结束、反斜杠的组合，避免只测试已知样例。
	for size in range(4):
		for characters in itertools.product("[]\\a/", repeat=size):
			value = "".join(characters)
			markup = (
				f"[bold]{display.escape_markup(value)}[/bold]{display.escape_markup(']')}{display.escape_markup('[a')}"
			)
			assert Text.from_markup(markup, emoji=False).plain == value + "][a"


@pytest.fixture
def rendered(monkeypatch):
	stream = io.StringIO()
	# 使用生产 Console 类型，覆盖 Text / Table 等不经 escape_markup 的路径。
	console = type(display.console)(file=stream, force_terminal=False, width=200, emoji=False)
	monkeypatch.setattr(display, "console", console)
	return stream


def _assert_no_control(text: str) -> None:
	assert display.strip_control(text) == text


def test_job_detail_with_adjacent_unfinished_tags(rendered) -> None:
	display.render_job_detail({"boss_name": "招聘者[/", "boss_title": "HRBP]"})
	assert "招聘者[/ (HRBP])" in rendered.getvalue()


@pytest.mark.parametrize("goal", ["ai_assist", "crawl_status"])
def test_wizard_text_paths_strip_terminal_controls(rendered, goal) -> None:
	renderer.render_run(
		{
			"status": "completed",
			"role": "candidate",
			"goal": goal,
			"last_result": {
				"data": {
					"status": "running",
					"query": "关键\x1b[2J词",
					"result": "建议\x1b[2J\x1b]0;标题\x07完",
					"resume": "cv\x9b31m",
				},
			},
		}
	)
	text = rendered.getvalue()
	_assert_no_control(text)
	assert "建议" in text if goal == "ai_assist" else "关键" in text


def test_plain_text_preserves_newlines_tabs_and_emoji_shortcodes(rendered) -> None:
	display.console.print(Text("第一行\n第二行\t尾部 :smile: [bold]"))
	text = rendered.getvalue()
	assert "第一行\n第二行" in text
	assert "尾部 :smile: [bold]" in text
	_assert_no_control(text)


def test_console_preserves_rich_style_and_control_segments() -> None:
	stream = io.StringIO()
	console = type(display.console)(file=stream, force_terminal=True, width=80, emoji=False, highlight=False)
	console.print(Text("正常\x1b[2J", style="red"))
	assert "\x1b[31m" in stream.getvalue()
	assert "\x1b[2J" not in stream.getvalue()
	control = Control.home()
	# Rich 自己产生的控制段需要保持原样，不能破坏进度条与状态指示器。
	segment = list(console.render(control))[0]
	assert segment.control
	assert segment.text == "\x1b[H"


@pytest.mark.parametrize("field", ["pages_completed", "next_page", "details_pending", "details_completed"])
def test_crawl_summary_with_text_in_count_field(rendered, field) -> None:
	renderer.render_run(
		{
			"status": "running",
			"role": "candidate",
			"goal": "crawl_status",
			"last_result": {"data": {"status": "running", "query": "q", field: "[/x]"}},
		}
	)
	assert "[/x]" in rendered.getvalue()


@pytest.mark.parametrize(
	"goal,data",
	[
		("digest", {"new_match_count": "[/x]", "follow_up_count": "[/x]", "interview_count": "[/x]"}),
		("watch", {"action": "run", "new_count": "[/x]", "total": "[/x]"}),
		("export", {"items": [{"title": "工程师"}], "count": "[/x]", "path": "jobs.csv"}),
	],
)
def test_other_result_counts_are_literal(rendered, goal, data) -> None:
	renderer.render_run({"status": "completed", "role": "candidate", "goal": goal, "last_result": {"data": data}})
	assert "[/x]" in rendered.getvalue()


@pytest.mark.parametrize(
	"skills,expected",
	[
		(7, "技能 -"),
		(None, "技能 -"),
		({"a": 1}, "技能 -"),
		("Java,Go", "技能 Java,Go"),
		(["Java", "Go"], "技能 Java、Go"),
		(("Java",), "技能 Java"),
	],
)
def test_job_card_handles_skill_shapes(rendered, skills: Any, expected: str) -> None:
	renderer.render_job_card_zh({"title": "工程师", "skills": skills})
	assert expected in rendered.getvalue()


def test_crawl_brief_handles_labels_and_count(rendered) -> None:
	renderer.render_crawl_job_brief({"labels": "Python", "crawl_page": "[/x]"})
	assert "标签 Python" in rendered.getvalue()
	assert "采集页 第 [/x] 页" in rendered.getvalue()


def test_click_menu_cleans_external_text_without_changing_selection(monkeypatch, capsys) -> None:
	monkeypatch.setattr(prompts.click, "prompt", lambda *args, **kwargs: "1")
	option = prompts.MenuOption("job-1", "后端\x1b[2J工程师\n  2. 结束浏览", "ACME\x1b]0;公司\x07\t北京")
	chosen = prompts.ClickMenu().select(
		"请选择\x1b[2J职位\n标题", [option], clear_before=False, allow_back=False, allow_exit=False
	)
	assert chosen == "job-1"
	text = capsys.readouterr().err
	_assert_no_control(text)
	assert "工程师   2. 结束浏览" in text
	assert not any(line.lstrip().startswith("2.") for line in text.splitlines())


def test_both_menu_drivers_receive_sanitized_options() -> None:
	original = prompts.MenuOption("unchanged-value", "工程师\n新行\x1b[2J", "说明\t内容\x07")
	items = prompts._augment_menu_options([original], allow_back=False, allow_exit=False)
	assert items[0].value == original.value
	assert items[0].label == "工程师 新行[2J"
	assert items[0].description == "说明 内容"
	assert original.label.endswith("\x1b[2J")
	text = "".join(part for _, part in prompts._menu_content_lines(items, selected=0, title="菜单"))
	_assert_no_control(text)
	assert "工程师 新行[2J" in text
