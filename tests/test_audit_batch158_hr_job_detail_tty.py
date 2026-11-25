"""hr jobs detail 的终端渲染（Issue #2774 收尾）。"""

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner, Result
from rich.console import Console

from boss_agent_cli import display
from boss_agent_cli.main import cli


def _tty(monkeypatch: pytest.MonkeyPatch, *, width: int = 110) -> io.StringIO:
	stream = io.StringIO()
	monkeypatch.setattr(display, "console", Console(file=stream, width=width, emoji=False))
	monkeypatch.setattr(display, "is_json_mode", display.json_forced)
	return stream


def _invoke(data_dir: Path, *args: str) -> Result:
	return CliRunner().invoke(cli, ["--role", "recruiter", "--data-dir", str(data_dir), *args])


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") if "zpData" in response else response.get("data")
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


@patch("boss_agent_cli.commands.recruiter.jobs.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.jobs.AuthManager")
def test_hr_jobs_detail_renders_sections_and_next_steps(
	mock_auth_cls, mock_platform_cls, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	stream = _tty(monkeypatch)
	platform = _ctx_mock(mock_platform_cls)
	platform.job_detail.return_value = {
		"code": 200,
		"zpData": {
			"jobName": "后端工程师",
			"jobDescription": "负责服务端开发",
			"salaryDesc": "25-40K",
			"cityName": "杭州",
		},
	}
	result = _invoke(tmp_path, "hr", "jobs", "detail", "job-enc-demo")
	assert result.exit_code == 0, result.output or repr(result.exception)
	assert not result.stdout
	out = stream.getvalue()
	for text in ("hr jobs detail", "后端工程师", "负责服务端开发", "25-40K", "杭州", "hr jobs offline"):
		assert text in out
	parsed = json.loads(_invoke(tmp_path, "--json", "hr", "jobs", "detail", "job-enc-demo").stdout)
	assert parsed["data"]["jobName"] == "后端工程师"
	assert parsed["hints"]["next_actions"][-1].endswith("hr jobs list")
