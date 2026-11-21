"""批次 91 审计修复：wizard 渲染与 live_crawl 产物探测边界（Issue #2368–#2371）。"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from boss_agent_cli import display
from boss_agent_cli.wizard import renderer
from boss_agent_cli.wizard.live_crawl import _output_paths


@pytest.fixture
def rendered(monkeypatch: pytest.MonkeyPatch):
	stream = io.StringIO()
	console = type(display.console)(file=stream, force_terminal=False, width=200, emoji=False)
	monkeypatch.setattr(display, "console", console)
	return stream


# ── Issue #2368：空结果面板不解析数据目录里的 markup ────────────────


def test_empty_result_panel_escapes_data_dir_markup(rendered: io.StringIO) -> None:
	def command_for(command: str) -> str:
		return f"boss --data-dir '/tmp/x[/bold]' {command}"

	renderer.render_run(
		{
			"status": "completed",
			"role": "candidate",
			"goal": "resumes",
			"last_result": {"data": {"items": []}},
		},
		command_for=command_for,
	)

	text = rendered.getvalue()
	assert "resume import" in text
	# 路径按字面渲染，不能被当成闭合标签吞掉。
	assert "[/bold]" in text


# ── Issue #2369：operator_actions 类型漂移按缺失处理 ─────────────────


@pytest.mark.parametrize("value", [5, "ab", {"step": "x"}])
def test_waiting_panel_ignores_drifted_operator_actions(
	rendered: io.StringIO, value: object
) -> None:
	renderer.render_run(
		{
			"status": "waiting_input",
			"role": "candidate",
			"goal": "crawl_jobs",
			"last_result": {"operator_actions": value, "data": {"status": "running"}},
		}
	)

	assert "需要你处理后再继续" in rendered.getvalue()


def test_waiting_panel_does_not_split_operator_action_string(rendered: io.StringIO) -> None:
	renderer.render_run(
		{
			"status": "waiting_input",
			"role": "candidate",
			"goal": "crawl_jobs",
			"last_result": {"operator_actions": "ab", "data": {"status": "running"}},
		}
	)

	# 类型漂移的字符串不能被逐字符渲染成一行一条假指引。
	lines = [line.strip("│ ") for line in rendered.getvalue().splitlines()]
	assert "a" not in lines
	assert "b" not in lines


def test_waiting_panel_renders_operator_actions_list(rendered: io.StringIO) -> None:
	renderer.render_run(
		{
			"status": "waiting_input",
			"role": "candidate",
			"goal": "crawl_jobs",
			"last_result": {
				"operator_actions": ["打开浏览器完成验证"],
				"data": {"status": "running"},
			},
		}
	)

	assert "打开浏览器完成验证" in rendered.getvalue()


# ── Issue #2370：artifacts 类型漂移按缺失处理 ───────────────────────


@pytest.mark.parametrize("value", [5, "/tmp/a.json", {"only": "a"}])
def test_status_table_ignores_drifted_artifacts(rendered: io.StringIO, value: object) -> None:
	renderer.render_run(
		{
			"status": "completed",
			"role": "candidate",
			"goal": "custom_goal",
			"last_result": {"artifacts": value},
		}
	)

	text = rendered.getvalue()
	assert "任务状态" in text
	# 字符串不能再被逐字符拆成「file t」「file m」之类的假产物。
	assert "file t" not in text


def test_status_table_renders_artifact_list(rendered: io.StringIO) -> None:
	renderer.render_run(
		{
			"status": "completed",
			"role": "candidate",
			"goal": "custom_goal",
			"last_result": {"artifacts": ["/tmp/a.json"]},
		}
	)

	text = rendered.getvalue()
	assert "产物" in text
	assert "a.json" in text


# ── Issue #2371：产物探测对权限错误不冒泡 ───────────────────────────


def test_output_paths_reports_existing_files(tmp_path: Path) -> None:
	(tmp_path / "jobs.json").write_text("[]", encoding="utf-8")

	assert _output_paths(str(tmp_path)) == {"json": str(tmp_path / "jobs.json")}


def test_output_paths_unreadable_directory_returns_empty(tmp_path: Path) -> None:
	blocked = tmp_path / "blocked"
	blocked.mkdir()
	os.chmod(blocked, 0)
	try:
		# 3.11 / 3.12 的 Path.exists() 会抛 PermissionError、3.14 静默 False；
		# 展示路径统一按「无法确认产物」处理（Issue #2371）。
		assert _output_paths(str(blocked)) == {}
	finally:
		os.chmod(blocked, 0o700)


def test_output_paths_invalid_path_returns_empty() -> None:
	assert _output_paths("/tmp/a\x00b") == {}
