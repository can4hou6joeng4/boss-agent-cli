"""重复页仍有后续时聚合必须标记不完整（Issue #2956）。

沟通列表按页码取数时可能反复返回同一页，旧实现保留早停，但无论重复页是否仍声明
``hasMore=true`` 都把聚合当作完整结果。``pipeline`` / ``follow-up`` / ``digest``
与向导同名步骤因此会把前缀当成全量。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.services.friend_list_pages import collect_friend_list_page
from boss_agent_cli.wizard.actions import DEFAULT_ACTIONS, ActionContext

NOW_MS = 1_780_000_000_000


class _RepeatPlatform:
	"""前两页返回同一联系人（securityId 每页轮换），按参数决定第二页是否还有后续。"""

	def __init__(self, *, second_has_more: bool) -> None:
		self.second_has_more = second_has_more
		self.calls: list[int] = []

	def __enter__(self) -> "_RepeatPlatform":
		return self

	def __exit__(self, *args: object) -> None:
		return None

	def friend_list(self, *, page: int = 1) -> dict[str, Any]:
		self.calls.append(page)
		item = {
			"uid": "contact-a",
			"securityId": f"sid_req{len(self.calls)}",
			"name": "甲",
			"brandName": "公司",
			"title": "招聘经理",
			"relationType": 1,
			"unreadMsgCount": 1,
			"lastMsg": "您好",
			"lastTS": NOW_MS - 3_600_000,
			"encryptJobId": "job-a",
		}
		has_more = True if page == 1 else self.second_has_more
		return {"code": 0, "zpData": {"result": [item], "hasMore": has_more}}

	def interview_data(self) -> dict[str, Any]:
		return {"code": 0, "zpData": {"interviewList": []}}

	@staticmethod
	def is_success(response: dict[str, Any]) -> bool:
		return response.get("code") == 0

	@staticmethod
	def unwrap_data(response: dict[str, Any]) -> Any:
		return response.get("zpData")


def test_duplicate_page_with_more_marks_prefix() -> None:
	platform = _RepeatPlatform(second_has_more=True)

	items, error, pages_scanned, truncated = collect_friend_list_page(platform)

	assert error is None
	assert platform.calls == [1, 2]
	assert pages_scanned == 2
	assert len(items) == 1
	assert truncated is True


def test_terminal_duplicate_page_stays_complete() -> None:
	platform = _RepeatPlatform(second_has_more=False)

	items, error, pages_scanned, truncated = collect_friend_list_page(platform)

	assert error is None
	assert platform.calls == [1, 2]
	assert pages_scanned == 2
	assert len(items) == 1
	assert truncated is False


@pytest.mark.parametrize(("module", "command"), [("pipeline", "pipeline"), ("pipeline", "follow-up")])
def test_commands_report_incomplete_duplicate_scan(module: str, command: str, tmp_path: Path) -> None:
	platform = _RepeatPlatform(second_has_more=True)
	with patch(f"boss_agent_cli.commands.{module}.AuthManager"), patch(
		f"boss_agent_cli.commands.{module}.get_platform_instance", return_value=platform
	):
		result = CliRunner().invoke(
			cli, ["--json", "--data-dir", str(tmp_path), command, "--now-ts-ms", str(NOW_MS)]
		)

	assert result.exit_code == 0, result.output
	envelope = json.loads(result.stdout)
	assert envelope["pagination"]["truncated"] is True
	assert envelope["pagination"]["pages_scanned"] == 2


def test_digest_reports_incomplete_duplicate_scan(tmp_path: Path) -> None:
	platform = _RepeatPlatform(second_has_more=True)
	with patch("boss_agent_cli.commands.digest.AuthManager"), patch(
		"boss_agent_cli.commands.digest.get_platform_instance", return_value=platform
	):
		result = CliRunner().invoke(
			cli, ["--json", "--data-dir", str(tmp_path), "digest", "--now-ts-ms", str(NOW_MS)]
		)

	assert result.exit_code == 0, result.output
	data = json.loads(result.stdout)["data"]
	assert data["truncated"] is True
	assert data["chat_pages_scanned"] == 2


def test_digest_markdown_describes_incomplete_scan(tmp_path: Path) -> None:
	platform = _RepeatPlatform(second_has_more=True)
	with patch("boss_agent_cli.commands.digest.AuthManager"), patch(
		"boss_agent_cli.commands.digest.get_platform_instance", return_value=platform
	):
		result = CliRunner().invoke(
			cli,
			["--json", "--data-dir", str(tmp_path), "digest", "--format", "md", "--now-ts-ms", str(NOW_MS)],
		)

	assert result.exit_code == 0, result.output
	content = json.loads(result.stdout)["data"]["content"]
	assert "未能完整翻完（已扫描前 2 页）" in content
	assert "页上限" not in content


def _wizard_context(tmp_path: Path, platform: _RepeatPlatform) -> ActionContext:
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		candidate_factory=lambda name, auth, delay, cdp_url, browser_source=None: platform,
	)


@pytest.mark.parametrize("action_name", ["candidate_pipeline", "candidate_digest"])
def test_wizard_actions_report_incomplete_duplicate_scan(action_name: str, tmp_path: Path) -> None:
	platform = _RepeatPlatform(second_has_more=True)

	result = DEFAULT_ACTIONS[action_name](_wizard_context(tmp_path, platform), {"now_ts_ms": NOW_MS}, {})

	assert result.data["truncated"] is True
	assert result.data["chat_pages_scanned"] == 2
