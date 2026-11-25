"""职位详情占位、JD 截断提示与命令整行输出（Issue #2767–#2768、#2770）。"""

import io

import pytest
from click.testing import CliRunner
from rich.console import Console

from boss_agent_cli import display
from boss_agent_cli.main import cli


def _tty(monkeypatch: pytest.MonkeyPatch, *, width: int = 100) -> io.StringIO:
	stream = io.StringIO()
	monkeypatch.setattr(display, "console", Console(file=stream, width=width, emoji=False))
	monkeypatch.setattr(display, "is_json_mode", display.json_forced)
	return stream


def test_job_detail_blank_fields_show_dash(monkeypatch: pytest.MonkeyPatch) -> None:
	stream = _tty(monkeypatch)
	display.render_job_detail(
		{
			"title": "示例职位",
			"salary": "",
			"experience": " ",
			"education": "",
			"city": "",
			"company": "",
			"boss_name": "",
			"boss_title": "",
		}
	)
	out = stream.getvalue()
	assert "exp: - | edu: - | city: -" in out
	assert "company: -" in out
	assert "boss: -" in out
	assert " ()" not in out


def test_job_detail_boss_line_omits_empty_title(monkeypatch: pytest.MonkeyPatch) -> None:
	stream = _tty(monkeypatch)
	display.render_job_detail({"title": "示例职位", "boss_name": "示例 HR", "boss_title": ""})
	boss_lines = [line for line in stream.getvalue().splitlines() if "boss:" in line]
	assert boss_lines and "示例 HR" in boss_lines[0] and "(" not in boss_lines[0]
	stream.seek(0)
	stream.truncate()
	display.render_job_detail({"title": "示例职位", "boss_name": "示例 HR", "boss_title": "招聘经理"})
	assert "boss: 示例 HR (招聘经理)" in stream.getvalue()


def test_job_table_blank_cells_show_dash(monkeypatch: pytest.MonkeyPatch) -> None:
	stream = _tty(monkeypatch)
	display.render_job_table([{"title": "", "company": "", "salary": ""}], "示例")
	row = [line for line in stream.getvalue().splitlines() if "│ 1" in line][0]
	cells = [cell.strip() for cell in row.split("│")[1:-1]]
	assert cells == ["1", "-", "-", "-", "-", "-", "-", "-"]


def test_job_detail_truncation_names_the_omitted_count(monkeypatch: pytest.MonkeyPatch) -> None:
	stream = _tty(monkeypatch)
	display.render_job_detail({"title": "示例职位", "description": "甲" * 620})
	out = stream.getvalue()
	assert out.count("甲") == 500
	assert "已省略 120 字" in out
	assert "--json" in out


def test_next_steps_keep_long_command_on_one_line(monkeypatch: pytest.MonkeyPatch) -> None:
	stream = _tty(monkeypatch, width=40)
	command = "boss --data-dir /opt/demo/very-long-workspace-name/another-segment/.boss-agent preset list"
	display.render_next_steps([command])
	assert any(command in line for line in stream.getvalue().splitlines())


def test_error_recovery_keeps_long_command_on_one_line(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
	stream = _tty(monkeypatch, width=48)
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "crawl", "status", "nope"])
	assert result.exit_code == 1
	recovery = f"boss --data-dir {tmp_path} crawl status"
	assert any(recovery in line for line in stream.getvalue().splitlines()), stream.getvalue()
