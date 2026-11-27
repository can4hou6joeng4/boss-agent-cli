"""favorites list 页码钳制与本地溢出分类回归（Issue #3032）。"""

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.services.pagination import MAX_TRUSTED_PAGE


def _platform(*, has_more: bool | str | None = True, page: int | None = None) -> MagicMock:
	platform = MagicMock()
	platform.__enter__.return_value = platform
	platform.is_success.return_value = True
	platform.unwrap_data.side_effect = lambda response: response["zpData"]
	data: dict[str, object] = {
		"cardList": [{"encryptJobId": "job_fixture", "jobName": "示例职位", "jobValidStatus": 1}],
		"hasMore": has_more,
	}
	if page is not None:
		data["page"] = page
	platform.job_favorites.return_value = {"zpData": data}
	return platform


def _invoke(tmp_path, platform: MagicMock, page: object):
	with patch("boss_agent_cli.commands.favorites.AuthManager"), patch(
		"boss_agent_cli.commands.favorites.get_platform_instance", return_value=platform
	):
		return CliRunner().invoke(
			cli, ["--json", "--data-dir", str(tmp_path), "favorites", "list", "--page", str(page)]
		)


@pytest.mark.parametrize("page", [MAX_TRUSTED_PAGE + 1, "9" * 4300])
def test_oversized_page_is_deterministic_invalid_param(tmp_path, page):
	"""超大页码必须在参数解析阶段确定失败，不能兜成可重试的 NETWORK_ERROR。"""
	platform = _platform()
	result = _invoke(tmp_path, platform, page)

	assert result.exit_code == 1
	payload = json.loads(result.stdout)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert payload["error"]["recoverable"] is False
	platform.job_favorites.assert_not_called()


def test_max_page_does_not_emit_unexecutable_next_hint(tmp_path):
	"""可信上限页仍声明有更多时，只给人工指引，不给 Agent 一条会溢出的翻页命令。"""
	result = _invoke(tmp_path, _platform(), MAX_TRUSTED_PAGE)

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is True
	assert payload["pagination"]["page"] == MAX_TRUSTED_PAGE
	assert not any("favorites list --page" in action for action in payload["hints"]["next_actions"])
	assert any("已达可信页码上限" in action for action in payload["hints"]["operator_actions"])


def test_platform_page_echo_is_clamped(tmp_path):
	"""平台回显超长页码时，信封回退到（已钳制的）请求页而不是照抄溢出值。"""
	platform = _platform(page=10**4300 - 1)
	result = _invoke(tmp_path, platform, 2)

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["pagination"]["page"] == 2
	assert payload["pagination"]["total"] == 1


def test_non_boolean_has_more_is_unknown(tmp_path):
	"""字符串 true 不是可靠分页证据，不能生成下一页命令。"""
	result = _invoke(tmp_path, _platform(has_more="true"), 2)

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["pagination"]["has_more"] is None
	assert payload["pagination"]["has_more_source"] == "unknown"
	assert not any("favorites list --page" in action for action in payload["hints"]["next_actions"])
