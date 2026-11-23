"""招聘者命令显式空筛选项不得静默变成“不筛选”（Issue #2598）。"""

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.api.recruiter_client import BossRecruiterClient
from boss_agent_cli.main import cli
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform


BLANK_CASES = [
	("applications", ["hr", "applications", "--job-id", ""]),
	("chat", ["hr", "chat", "--job-id", "", "--summary-limit", "0"]),
	("chat", ["hr", "last-messages", "--job-id", "", "--limit", "1"]),
	("candidates", ["hr", "candidates", "py", "--job-id", "", "--city", ""]),
]

OMITTED_CASES = [
	("applications", ["hr", "applications"]),
	("chat", ["hr", "chat", "--summary-limit", "0"]),
	("chat", ["hr", "last-messages", "--limit", "1"]),
	("candidates", ["hr", "candidates", "py"]),
]


def _invoke(module: str, args: list[str], data_dir: Any, client: Any | None = None) -> tuple[dict[str, Any], MagicMock | None]:
	factory: MagicMock | None = None
	with patch(f"boss_agent_cli.commands.recruiter.{module}.AuthManager"), \
			patch(f"boss_agent_cli.commands.recruiter.{module}.get_recruiter_platform_instance") as patched:
		factory = patched
		if client is not None:
			patched.return_value.__enter__.return_value = BossRecruiterPlatform(client)
		result = CliRunner().invoke(cli, ["--data-dir", str(data_dir), "--json", *args])
	return json.loads(result.output), factory


@pytest.mark.parametrize(("module", "args"), BLANK_CASES)
def test_blank_filter_is_rejected_before_platform(tmp_path, module, args):
	envelope, factory = _invoke(module, args, tmp_path)

	assert envelope["ok"] is False
	assert envelope["error"]["code"] == "INVALID_PARAM"
	assert "不能为空" in envelope["error"]["message"]
	assert factory is not None
	factory.assert_not_called()


@pytest.mark.parametrize(("module", "args"), OMITTED_CASES)
def test_omitted_filter_keeps_unfiltered_behavior(tmp_path, module, args):
	auth = MagicMock()
	auth.get_token.return_value = {"cookies": {"wt2": "fake"}, "stoken": "s", "user_agent": "UA"}
	client = BossRecruiterClient(auth, delay=(0, 0))
	sent: list[dict[str, Any]] = []

	def fake_request(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
		sent.append({**(kwargs.get("data") or {}), **(kwargs.get("params") or {})})
		return {"code": 0, "zpData": {"friendList": [], "geekList": [], "hasMore": False}}

	with patch.object(client, "_request", side_effect=fake_request), patch.object(client, "is_browser_only", return_value=False):
		envelope, _ = _invoke(module, args, tmp_path, client)

	assert envelope["ok"] is True
	assert sent
