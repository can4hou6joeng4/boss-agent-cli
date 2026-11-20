"""个人数据接口的形状漂移：jobCard / zpData 非对象时返回结构化结果而不是 INTERNAL_ERROR。

`show` 的 `jobCard`、`me --section user` 与 `status --live` 的 `user_info.zpData`
此前直接 `.get` 取子字段；平台返回字符串或数组时抛 `AttributeError`，
被通用兜底成 `INTERNAL_ERROR`。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli


class _FakePlatform:
	def __init__(self, *, zp_data: object = None, user_info: object = None):
		self._zp_data = zp_data
		self._user_info = user_info

	def __enter__(self):
		return self

	def __exit__(self, *exc):
		return None

	def job_card(self, security_id, lid):
		return {"code": 0, "zpData": self._zp_data}

	def user_info(self):
		return {"code": 0, "zpData": self._user_info}

	def is_success(self, response):
		return isinstance(response, dict) and response.get("code") == 0

	def unwrap_data(self, response):
		return response.get("zpData") if isinstance(response, dict) else None


def _show(platform: _FakePlatform, *args: str):
	with patch("boss_agent_cli.commands.show.get_platform_instance", return_value=platform), \
		patch("boss_agent_cli.commands.show.AuthManager"), \
		patch("boss_agent_cli.commands.show.CacheStore") as cache_cls, \
		patch(
			"boss_agent_cli.commands.show.get_index_entry",
			return_value=(
				{"exists": True, "source": "search:x", "count": 1, "saved_at": 1.0, "page": None},
				{"security_id": "s1", "lid": "l1"},
			),
		):
		cache_cls.return_value.__enter__.return_value.is_greeted.return_value = False
		return CliRunner().invoke(cli, ["--json", "show", "1", *args])


@pytest.mark.parametrize("zp_data", ["oops", ["oops"], {"jobCard": "oops"}], ids=["zpData-str", "zpData-list", "jobCard-str"])
def test_show_treats_drifted_job_card_as_job_not_found(zp_data):
	result = _show(_FakePlatform(zp_data=zp_data))

	assert result.exit_code == 1, result.output
	envelope = json.loads(result.stdout)
	assert envelope["ok"] is False
	assert envelope["error"]["code"] == "JOB_NOT_FOUND"


def test_show_still_returns_a_valid_card():
	card = {"encryptJobId": "j1", "jobName": "Go 开发", "brandName": "TestCo"}
	result = _show(_FakePlatform(zp_data={"jobCard": card}))

	assert result.exit_code == 0, result.output
	envelope = json.loads(result.stdout)
	assert envelope["ok"] is True
	assert envelope["data"]["title"] == "Go 开发"


@pytest.mark.parametrize("zp_data", ["oops", [1, 2]], ids=["str", "list"])
def test_me_user_section_treats_drifted_zp_data_as_empty(zp_data):
	with patch("boss_agent_cli.commands.me.get_platform_instance", return_value=_FakePlatform(user_info=zp_data)), \
		patch("boss_agent_cli.commands.me.AuthManager"):
		result = CliRunner().invoke(cli, ["--json", "me", "--section", "user"])

	assert result.exit_code == 0, result.output
	envelope = json.loads(result.stdout)
	assert envelope["ok"] is True
	assert envelope["data"]["user"] == {
		"name": "",
		"email": "",
		"phone": "",
		"identity": "",
		"avatar": "",
	}


@pytest.mark.parametrize("identity", [0, 1])
def test_me_user_section_preserves_identity_type(identity):
	with patch("boss_agent_cli.commands.me.get_platform_instance", return_value=_FakePlatform(user_info={"identity": identity})), \
		patch("boss_agent_cli.commands.me.AuthManager"):
		result = CliRunner().invoke(cli, ["--json", "me", "--section", "user"])

	assert result.exit_code == 0, result.output
	user = json.loads(result.stdout)["data"]["user"]
	assert user["identity"] == identity
	assert type(user["identity"]) is int


@pytest.mark.parametrize("zp_data", ["oops", [1]], ids=["str", "list"])
def test_status_live_treats_drifted_user_info_as_unknown(zp_data):
	with patch(
		"boss_agent_cli.commands.status.get_platform_instance",
		return_value=_FakePlatform(user_info=zp_data),
	), patch("boss_agent_cli.commands.status.AuthManager") as auth_cls, patch(
		"boss_agent_cli.commands.status.assess_auth_health"
	) as health:
		auth_cls.return_value.check_status.return_value = {"logged_in": True, "token_expires_in": 3600}
		health.return_value = MagicMock(
			auth_state="logged_in",
			summary="ok",
			public_summary=lambda: {},
			checks_as_dicts=lambda: [],
		)
		result = CliRunner().invoke(cli, ["--json", "status", "--live"])

	assert result.exit_code == 0, result.output
	envelope = json.loads(result.stdout)
	assert envelope["ok"] is True
	assert envelope["data"]["user_name"] == "未知用户"
