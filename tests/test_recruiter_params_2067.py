"""招聘者命令参数接缝（Issue #2067–#2069）。"""

from __future__ import annotations

import json
import shlex
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform

HUGE_PAGE = int("9" * 4300)


def _run(tmp_path, *args):
	return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])


def _platform(factory):
	factory.return_value.__enter__.return_value = BossRecruiterPlatform(factory._client)# ── Issue #2067：hr jobs list 超大 page 必须钳制，不能报 NETWORK_ERROR ──


def test_hr_jobs_list_huge_page_is_clamped_not_network_error(tmp_path) -> None:
	client = MagicMock()
	client.list_jobs.return_value = {"code": 0, "zpData": {"hasMore": True, "totalSize": 0, "data": []}}
	with (
		patch("boss_agent_cli.commands.recruiter.jobs.AuthManager"),
		patch("boss_agent_cli.commands.recruiter.jobs.get_recruiter_platform_instance") as factory,
	):
		factory.return_value.__enter__.return_value = BossRecruiterPlatform(client)
		result = _run(tmp_path, "hr", "jobs", "list", "--page", str(HUGE_PAGE))

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is True
	assert payload["pagination"]["page"] == 1_000_000
	# 钳制后的页码已到可信上限：再提示 --page 1000001 只会被再次钳回同一页，
	# Agent 会无界重复同一请求（Issue #2434），因此上限页不再生成下一页命令。
	assert payload["pagination"]["has_more"] is True
	assert not any("--page 1000001" in action for action in payload["hints"]["next_actions"])


def test_hr_jobs_list_platform_huge_page_is_clamped(tmp_path) -> None:
	client = MagicMock()
	client.list_jobs.return_value = {"code": 0, "zpData": {"page": HUGE_PAGE, "hasMore": True, "data": []}}
	with (
		patch("boss_agent_cli.commands.recruiter.jobs.AuthManager"),
		patch("boss_agent_cli.commands.recruiter.jobs.get_recruiter_platform_instance") as factory,
	):
		factory.return_value.__enter__.return_value = BossRecruiterPlatform(client)
		result = _run(tmp_path, "hr", "jobs", "list", "--page", "2")

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["pagination"]["page"] == 2


# ── Issue #2068：横杠开头 query 的翻页提示必须可重放 ─────────────────


def test_hr_candidates_dashed_query_replay_keeps_query_and_select(tmp_path) -> None:
	client = MagicMock()
	client.search_geeks.return_value = {"code": 0, "zpData": {"geekList": [], "hasMore": True}}
	with (
		patch("boss_agent_cli.commands.recruiter.candidates.AuthManager"),
		patch("boss_agent_cli.commands.recruiter.candidates.get_recruiter_platform_instance") as factory,
		patch("boss_agent_cli.commands.recruiter.candidates.require_compliance_allowed", return_value=True),
	):
		factory.return_value.__enter__.return_value = BossRecruiterPlatform(client)
		result = _run(tmp_path, "hr", "candidates", "--page", "1", "--", "--select")

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	page_action = next(action for action in payload["hints"]["next_actions"] if "--page 2" in action)
	parts = shlex.split(page_action)
	assert parts[-2:] == ["--", "--select"], page_action

	# 重放同一串参数：query 仍是 --select，select 开关不被误开。
	client.search_geeks.reset_mock()
	with (
		patch("boss_agent_cli.commands.recruiter.candidates.AuthManager"),
		patch("boss_agent_cli.commands.recruiter.candidates.get_recruiter_platform_instance") as factory2,
		patch("boss_agent_cli.commands.recruiter.candidates.require_compliance_allowed", return_value=True),
	):
		factory2.return_value.__enter__.return_value = BossRecruiterPlatform(client)
		replay = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", *parts[3:]])
	assert replay.exit_code == 0, replay.output
	args = client.search_geeks.call_args
	assert args.args[0] == "--select"
	assert args.kwargs["select"] is False
	assert args.kwargs["page"] == 2


# ── Issue #2069：hr resume 只点名真正缺失的参数 ──────────────────────


def _invoke_resume(tmp_path, *args):
	with (
		patch("boss_agent_cli.commands.recruiter.resume.AuthManager"),
		patch("boss_agent_cli.commands.recruiter.resume.get_recruiter_platform_instance") as factory,
		patch("boss_agent_cli.commands.recruiter.resume.require_compliance_allowed", return_value=True),
	):
		factory.return_value.__enter__.return_value = BossRecruiterPlatform(MagicMock())
		return _run(tmp_path, "hr", "resume", *args)


def test_hr_resume_missing_geek_id_names_only_geek_id(tmp_path) -> None:
	result = _invoke_resume(tmp_path, "--job-id", "j1", "--security-id", "sec1")

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert payload["error"]["message"] == "查看简历缺少参数：geek_id"
	assert "hr candidates" in payload["error"]["recovery_action"]
	assert "hr applications" not in payload["error"]["recovery_action"]
	assert payload["hints"]["next_actions"][0].endswith("hr candidates")


def test_hr_resume_missing_security_id_names_only_security_id(tmp_path) -> None:
	result = _invoke_resume(tmp_path, "g1", "--job-id", "j1")

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["message"] == "查看简历缺少参数：--security-id"
	assert "--job-id" not in payload["error"]["message"]
	assert "hr applications" in payload["error"]["recovery_action"]


def test_hr_resume_missing_all_names_all_three(tmp_path) -> None:
	result = _invoke_resume(tmp_path)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["message"] == "查看简历缺少参数：geek_id, --job-id, --security-id"
