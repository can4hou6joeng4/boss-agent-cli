"""终端结果的折行、字段展开与脱敏（Issue #2761–#2765）。"""

import datetime
import io
import json
from pathlib import Path

import pytest
from click.testing import CliRunner, Result
from rich.console import Console

from boss_agent_cli import display
from boss_agent_cli.commands import watch
from boss_agent_cli.main import cli
from boss_agent_cli.search_filters import SearchPipelinePlatformError


def _tty(monkeypatch: pytest.MonkeyPatch, *, width: int = 100) -> io.StringIO:
	stream = io.StringIO()
	monkeypatch.setattr(display, "console", Console(file=stream, width=width, emoji=False))
	monkeypatch.setattr(display, "is_json_mode", display.json_forced)
	return stream


def _invoke(data_dir: Path, *args: str) -> Result:
	result = CliRunner().invoke(cli, ["--data-dir", str(data_dir), *args])
	assert result.exit_code == 0, result.output or repr(result.exception)
	return result


@pytest.mark.parametrize("width", [80, 100])
def test_table_keeps_unbroken_text(monkeypatch: pytest.MonkeyPatch, width: int) -> None:
	stream = _tty(monkeypatch, width=width)
	message = "您好，看了您的简历觉得很合适，想约您周四下午三点到公司面试，地址在示例路。"
	path = "/opt/demo/runtime/browser-environment/.venv/bin/python"
	display.render_simple_list(
		[{"name": "browser_kernel", "text": message}, {"name": "repair", "text": path}],
		"检查结果",
		[("名称", "name", "cyan"), ("说明", "text", "")],
	)
	body = "".join(line.split("│")[3].strip() for line in stream.getvalue().splitlines() if "│" in line)
	assert body == message + path


def test_panels_display_missing_values_and_preserve_falsy_scalars(monkeypatch: pytest.MonkeyPatch) -> None:
	stream = _tty(monkeypatch)
	display.render_message_panel({"items": ["方案甲", "方案乙"], "missing": None, "empty": {}})
	display.render_sectioned_record(
		{
			"budget": {"attempts": 0, "elapsed": 0.0, "complete": False, "limits": {"pages": 20}},
			"hooks": [],
		}
	)
	out = stream.getvalue()
	for text in (
		"items: 方案甲, 方案乙",
		"missing: -",
		"empty: -",
		"attempts: 0",
		"elapsed: 0.0",
		"complete: False",
		"limits:",
		"pages: 20",
		"hooks: -",
	):
		assert text in out
	assert "{" not in out


@pytest.mark.parametrize("family", ["preset", "watch"])
def test_saved_search_tty_only_lists_configured_filters(
	monkeypatch: pytest.MonkeyPatch,
	tmp_path: Path,
	family: str,
) -> None:
	stream = _tty(monkeypatch)
	added = _invoke(tmp_path, family, "add", "后端", "golang", "--city", "杭州", "--active", "3d")
	assert not added.stdout
	added_output = stream.getvalue()
	stream.seek(0)
	stream.truncate()
	listed = _invoke(tmp_path, family, "list")
	assert not listed.stdout
	for output in (added_output, stream.getvalue()):
		assert "query: golang" in output
		assert "city: 杭州" in output
		assert "active: 3d" in output
		assert "salary" not in output
		assert "null" not in output

	row = json.loads(_invoke(tmp_path, "--json", family, "list").stdout)["data"][0]
	assert row["params"]["salary"] is None
	assert row["params"]["active"] == "3d"
	stamp = datetime.datetime.fromtimestamp(row["created_at"]).strftime("%Y-%m-%d %H:%M")
	assert stamp in stream.getvalue()


def test_resume_applications_uses_local_time(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
	stream = _tty(monkeypatch)
	_invoke(tmp_path, "--json", "resume", "init", "--name", "demo")
	_invoke(tmp_path, "--json", "resume", "link", "demo", "sid-demo", "job-demo", "--title", "开发示例")
	row = json.loads(_invoke(tmp_path, "--json", "resume", "applications", "demo").stdout)["data"][0]
	_invoke(tmp_path, "resume", "applications", "demo")
	stamp = datetime.datetime.fromtimestamp(row["updated_at"]).strftime("%Y-%m-%d %H:%M")
	assert stamp in stream.getvalue()


def test_watch_all_displays_jobs_and_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
	stream = _tty(monkeypatch, width=120)
	for name in ("后端", "数据"):
		_invoke(tmp_path, "--json", "watch", "add", name, "python")

	def run_watch(_ctx, _cache, name):
		if name == "数据":
			raise SearchPipelinePlatformError("SAVED_SEARCH_INVALID", "示例筛选条件已失效")
		return {
			"name": name,
			"new_count": 1,
			"seen_count": 0,
			"total_count": 1,
			"new_items": [{"title": "后端示例职位", "company": "示例公司"}],
		}

	monkeypatch.setattr(watch, "_execute_single_watch", run_watch)
	result = _invoke(tmp_path, "watch", "run", "--all")
	assert not result.stdout
	out = stream.getvalue()
	for text in ("后端示例职位", "SAVED_SEARCH_INVALID", "示例筛选条件已失效", "watch list"):
		assert text in out
	data = json.loads(_invoke(tmp_path, "--json", "watch", "run", "--all").stdout)["data"]
	assert data["invalid"] == ["数据"]
	assert data["total"] == 2


def test_tty_config_redacts_url_in_message_panel_and_custom_renderer(
	monkeypatch: pytest.MonkeyPatch,
	tmp_path: Path,
) -> None:
	stream = _tty(monkeypatch, width=120)
	_invoke(
		tmp_path,
		"--json",
		"ai",
		"local",
		"configure",
		"--runtime",
		"ollama",
		"--model",
		"qwen3:8b",
		"--base-url",
		"http://demo-user:demo-pass@example.invalid:11434/v1",
	)
	for args in (("ai", "config"), ("ai", "local", "status")):
		stream.seek(0)
		stream.truncate()
		result = _invoke(tmp_path, *args)
		assert not result.stdout
		assert "demo-pass" not in stream.getvalue()
		assert "demo-user:[REDACTED]@example.invalid" in stream.getvalue()
