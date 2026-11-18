"""`stats` / `digest` 的 `-o` 拒绝纯空白路径（Issue #1788）。"""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli


@pytest.mark.parametrize(
	"argv",
	[
		["stats", "--format", "html"],
		["digest", "--format", "md"],
	],
)
@pytest.mark.parametrize("blank", ["  ", "\t", " \n "])
def test_blank_output_path_is_rejected_before_any_write(tmp_path, monkeypatch, argv, blank):
	workdir = tmp_path / "cwd"
	workdir.mkdir()
	monkeypatch.chdir(workdir)
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path / "data"), "--json", *argv, "-o", blank])
	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "不能为空" in payload["error"]["message"]
	assert os.listdir(workdir) == []


def test_stats_html_still_writes_a_named_file(tmp_path, monkeypatch):
	monkeypatch.chdir(tmp_path)
	target = tmp_path / "report.html"
	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path / "data"), "--json", "stats", "--format", "html", "-o", str(target)]
	)
	assert result.exit_code == 0, result.output
	assert json.loads(result.output)["data"]["path"] == str(target)
	assert target.is_file()
