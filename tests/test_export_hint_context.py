"""导出命令的后继与恢复提示在产生它的数据目录中直接重放。"""

import json
import shlex
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.auth.manager import AuthRequired
from boss_agent_cli.main import cli

_JOB = {
	"encryptJobId": "job_fixture",
	"securityId": "sid_fixture",
	"jobName": "测试职位",
	"brandName": "公司A",
	"salaryDesc": "20K",
	"cityName": "北京",
	"jobExperience": "3-5年",
	"jobDegree": "本科",
	"bossName": "HR",
	"bossTitle": "招聘",
	"bossOnline": True,
}


@pytest.fixture
def export_context(tmp_path):
	data_dir = tmp_path / "jobs [bold] team's"
	platform = MagicMock()
	platform.name = "zhipin"
	platform.__enter__.return_value = platform
	platform.is_success.return_value = True
	platform.unwrap_data.side_effect = lambda raw: raw["zpData"]
	platform.search_jobs.return_value = {"zpData": {"hasMore": False, "jobList": [_JOB]}}
	cache = MagicMock()
	cache.__enter__.return_value = cache
	cache.get_search.return_value = None
	auth = MagicMock()
	auth.account_fingerprint.return_value = "account_fixture"

	def authenticated_in_original_directory(path, **kwargs):
		# 只有原目录有登录态：提示丢了 --data-dir 时，重放必须失败。
		if Path(path).resolve() != data_dir.resolve():
			raise AuthRequired("测试登录态仅存在于原数据目录")
		return auth

	with ExitStack() as stack:
		for module in ("export", "search"):
			stack.enter_context(patch(
				f"boss_agent_cli.commands.{module}.AuthManager",
				side_effect=authenticated_in_original_directory,
			))
			stack.enter_context(patch(f"boss_agent_cli.commands.{module}.get_platform_instance", return_value=platform))
		stack.enter_context(patch("boss_agent_cli.commands.search.CacheStore", return_value=cache))
		pipeline = stack.enter_context(patch("boss_agent_cli.commands.search.run_search_pipeline"))
		pipeline.return_value = SimpleNamespace(
			items=[{"job_id": "job_fixture", "security_id": "sid_fixture", "title": "测试职位"}],
			has_more=False, has_more_reported=False, total=1,
			stats=SimpleNamespace(pages_scanned=1, jobs_seen=1, jobs_prefiltered=0, detail_checks=0),
		)
		yield data_dir


def _run(data_dir, *args):
	return CliRunner().invoke(cli, ["--data-dir", str(data_dir), "--json", *args])


def _hint_args(action, data_dir, **placeholders):
	"""把提示还原成 argv：前缀必须是原目录，占位符换成真实取值。"""
	parts = shlex.split(action)
	assert parts[:3] == ["boss", "--data-dir", str(data_dir.resolve())]
	values = {"<query>": "Python", "<关键词>": "Python", **placeholders}
	return [values.get(part, part) for part in parts[1:]]


def _replay(action, data_dir, **placeholders):
	# 只使用提示里的参数，调用方不补回 --data-dir。
	result = CliRunner().invoke(cli, ["--json", *_hint_args(action, data_dir, **placeholders)])
	assert result.exit_code == 0, result.output
	return json.loads(result.stdout)


def test_export_file_followups_keep_original_directory(export_context, tmp_path):
	data_dir = export_context
	result = _run(data_dir, "export", "Python", "-o", str(tmp_path / "jobs.csv"))
	assert result.exit_code == 0, result.output
	actions = json.loads(result.stdout)["hints"]["next_actions"]
	commands = [_hint_args(action, data_dir)[2] for action in actions]
	assert commands == ["search", "recommend"]
	# 登录态只在原目录：search 提示丢了目录就会在 AuthManager 处失败。
	assert _replay(actions[0], data_dir)["ok"] is True


def test_export_stdout_followup_replays_in_original_directory(export_context, tmp_path):
	data_dir = export_context
	result = _run(data_dir, "export", "Python")
	assert result.exit_code == 0, result.output
	(action,) = json.loads(result.stdout)["hints"]["next_actions"]
	output = tmp_path / "jobs.csv"
	payload = _replay(action, data_dir, **{"file.csv": str(output)})
	assert payload["data"]["path"] == str(output)
	assert output.exists()


def test_export_html_content_followup_replays_in_original_directory(export_context, tmp_path):
	data_dir = export_context
	result = _run(data_dir, "export", "Python", "--format", "html")
	assert result.exit_code == 0, result.output
	(action,) = json.loads(result.stdout)["hints"]["next_actions"]
	output = tmp_path / "jobs.html"
	payload = _replay(action, data_dir, **{"<path>": str(output)})
	assert payload["data"]["format"] == "html"
	assert output.exists()


def test_export_unparsable_url_retry_replays_in_original_directory(export_context):
	data_dir = export_context
	result = _run(data_dir, "export", "--url", "not-a-boss-url")
	assert result.exit_code == 1, result.output
	parsed = json.loads(result.stdout)
	(action,) = parsed["hints"]["next_actions"]
	assert action in parsed["error"]["recovery_action"]
	assert _replay(action, data_dir)["ok"] is True


@pytest.mark.parametrize(
	("args", "message", "has_next_action"),
	[
		((), "未提供 query", False),
		(("--url", ""), "--url 的值不能为空", True),
	],
)
def test_export_argument_errors_point_at_same_directory(export_context, args, message, has_next_action):
	data_dir = export_context
	result = _run(data_dir, "export", *args)
	assert result.exit_code == 1, result.output
	parsed = json.loads(result.stdout)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert message in parsed["error"]["message"]
	# 恢复正文里给了两条示例命令，两条都必须指向原目录。
	parts = shlex.split(parsed["error"]["recovery_action"])
	directories = [parts[index + 1] for index, part in enumerate(parts) if part == "--data-dir"]
	assert directories == [str(data_dir.resolve())] * 2
	assert parts.count("export") == 2
	if has_next_action:
		(action,) = parsed["hints"]["next_actions"]
		assert _replay(action, data_dir)["ok"] is True
	else:
		assert parsed["hints"] is None
