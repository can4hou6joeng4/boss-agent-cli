"""exchange / show 的类型漂移回归（Issue #1812 #1813）。"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.index_cache import save_index
from boss_agent_cli.main import cli


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") if "zpData" in response else response.get("data")
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


def _friends(**overrides):
	item = {"uid": 10001, "securityId": "sid_now", "name": "联系人甲", "friendSource": 0, "encryptJobId": "job_001"}
	item.update(overrides)
	return {"zpData": {"result": [item], "friendList": [item]}}


def _exchange(tmp_path, identifier, friends):
	with patch("boss_agent_cli.commands.exchange.AuthManager"), patch(
		"boss_agent_cli.commands.exchange.get_platform_instance"
	) as mock_cls:
		client = _ctx_mock(mock_cls)
		client.friend_list.return_value = friends
		client.exchange_contact.return_value = {"zpData": {}}
		out = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "exchange", "--", identifier])
	return out, client


@pytest.mark.parametrize("name", [{"n": 1}, 42, True, None, "   "])
def test_exchange_drifted_name_falls_back_to_placeholder(tmp_path, name):
	out, client = _exchange(tmp_path, "10001", _friends(name=name))
	assert out.exit_code == 0, out.output
	payload = json.loads(out.output)
	assert payload["data"]["name"] == "-"
	assert client.exchange_contact.call_args.args[2] == "-"


def test_exchange_keeps_real_name(tmp_path):
	out, client = _exchange(tmp_path, "10001", _friends(name=" 联系人甲 "))
	assert json.loads(out.output)["data"]["name"] == "联系人甲"


def test_exchange_container_security_id_stops_before_write(tmp_path):
	out, client = _exchange(tmp_path, "10001", _friends(securityId={"a": 1}))
	assert out.exit_code == 1
	assert json.loads(out.output)["error"]["code"] == "NETWORK_ERROR"
	client.exchange_contact.assert_not_called()


def test_exchange_negative_uid_hint_is_replayable(tmp_path):
	out, _ = _exchange(tmp_path, "-5", _friends(uid=-5))
	assert out.exit_code == 0, out.output
	hints = json.loads(out.output)["hints"]["next_actions"]
	assert hints[1].endswith("chatmsg -- -5")


# ── #1813 ────────────────────────────────────────────────────


def _show(tmp_path, jobs, card=None):
	save_index(tmp_path, jobs, source="search")
	with patch("boss_agent_cli.commands.show.AuthManager"), patch(
		"boss_agent_cli.commands.show.get_platform_instance"
	) as mock_cls:
		client = _ctx_mock(mock_cls)
		client.job_card.return_value = {"code": 0, "zpData": {"jobCard": card or {"encryptJobId": "j1", "jobName": "t"}}}
		out = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "show", "1"])
	return out, client


@pytest.mark.parametrize("sid", [{"a": 1}, 12345, True, "  ", None])
def test_show_rejects_non_string_security_id_without_request(tmp_path, sid):
	out, client = _show(tmp_path, [{"security_id": sid, "job_id": "j", "lid": ""}])
	assert out.exit_code == 1
	payload = json.loads(out.output)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "缺少 security_id" in payload["error"]["message"]
	client.job_card.assert_not_called()


def test_show_drops_non_string_lid(tmp_path):
	out, client = _show(tmp_path, [{"security_id": "s", "job_id": {"x": 1}, "lid": {"q": 1}}], card={"encryptJobId": "j1", "jobName": "t", "lid": {"z": 1}})
	assert out.exit_code == 0, out.output
	assert client.job_card.call_args.args == ("s", "")
	assert json.loads(out.output)["data"]["lid"] == ""


def test_show_valid_row_still_works(tmp_path):
	out, client = _show(tmp_path, [{"security_id": "s", "job_id": "j", "lid": "L"}])
	assert out.exit_code == 0, out.output
	assert client.job_card.call_args.args == ("s", "L")
	assert json.loads(out.output)["data"]["lid"] == "L"
