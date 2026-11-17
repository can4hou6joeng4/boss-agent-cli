"""Issue #1485：招聘者聊天动作必须保留 friend_detail 的平台错误码。

httpx 层用假客户端，浏览器动作全部替身；覆盖 reply / exchange 两个写动作
在 401 / 9 / 37 / 无唯一匹配下的稳定错误码，以及成功路径不变。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.api.recruiter_client import BossRecruiterClient
from boss_agent_cli.main import cli
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform

FRIEND_ID = 12345
FRIEND = {
	"uid": FRIEND_ID,
	"encryptUid": "enc-u",
	"encryptJobId": "enc-j",
	"securityId": "sec-s",
	"friendSource": 0,
	"name": "Tester",
}
FRIEND_DETAIL_OK = {"code": 0, "zpData": {"friendList": [FRIEND]}}
FRIEND_DETAIL_EMPTY = {"code": 0, "zpData": {"friendList": []}}


class FakeCookieJar:
	def items(self):
		return {}.items()

	def set(self, name: str, value: str) -> None:
		pass

	def get(self, name: str):
		return None


class FakeResponse:
	def __init__(self, *, status_code: int = 200, payload: dict | None = None, text: str = ""):
		self.status_code = status_code
		self._payload = payload if payload is not None else {"code": 0}
		self.text = text
		self.cookies = FakeCookieJar()

	def json(self):
		return self._payload

	def raise_for_status(self):
		if self.status_code >= 400:
			raise RuntimeError(f"HTTP {self.status_code}")


class FakeHttpxClient:
	def __init__(self, responses: list[FakeResponse]):
		self.responses = list(responses)
		self.calls: list[dict] = []
		self.cookies = FakeCookieJar()

	def request(self, method: str, url: str, headers: dict | None = None, **kwargs):
		self.calls.append({"method": method, "url": url, "headers": headers, "kwargs": kwargs})
		return self.responses.pop(0)

	def close(self) -> None:
		pass


class FakeAuth:
	def __init__(self) -> None:
		self.token = {"cookies": {"wt2": "cookie"}, "stoken": "initial-stoken", "user_agent": "ua"}
		self.refresh_calls = 0

	def get_token(self):
		return self.token

	def force_refresh(self, cdp_url: str | None = None, browser_source: str | None = None):
		self.refresh_calls += 1
		self.token = {**self.token, "stoken": f"refreshed-{self.refresh_calls}"}


def _client(fake: FakeHttpxClient, auth: FakeAuth | None = None) -> BossRecruiterClient:
	client = BossRecruiterClient(auth or FakeAuth())
	client._client = fake
	client._throttle.wait = lambda: None
	client._throttle.mark = lambda: None
	return client


def _invoke(client: BossRecruiterClient, action: str) -> dict:
	if action == "reply":
		return client.send_message_by_friend(FRIEND_ID, "你好")
	return client.exchange_request_by_friend(FRIEND_ID, exchange_type=2)


@pytest.mark.parametrize("action", ["reply", "exchange"])
@pytest.mark.parametrize(
	("payload", "expected_code"),
	[
		({"code": 401, "message": "平台认证已失效，请重新登录", "zpData": {}}, "AUTH_EXPIRED"),
		({"code": 9, "message": "请求频率过高", "zpData": {}}, "RATE_LIMITED"),
		({"code": 37, "message": "stoken 已过期，请重新登录", "zpData": {}}, "TOKEN_REFRESH_FAILED"),
	],
)
def test_friend_detail_platform_code_reaches_parse_error(action, payload, expected_code):
	"""friend_detail 的 401 / 9 / 37 原样交给 parse_error，不再退化成 UNKNOWN。"""
	fake = FakeHttpxClient([FakeResponse(payload=dict(payload)) for _ in range(4)])
	auth = FakeAuth()
	client = _client(fake, auth) if payload["code"] == 401 else BossRecruiterClient(auth)
	if payload["code"] != 401:
		client._throttle.wait = lambda: None
		client._throttle.mark = lambda: None
	client._get_browser = MagicMock()  # type: ignore[method-assign]

	with (
		patch("boss_agent_cli.api._base_client.cancellable_sleep"),
		patch("boss_agent_cli.api._base_client.httpx.Client", return_value=fake),
	):
		result = _invoke(client, action)

	assert result["code"] == payload["code"]
	assert BossRecruiterPlatform(client).parse_error(result)[0] == expected_code
	assert result["__cli_error_details__"]["action"] == action
	client._get_browser.assert_not_called()
	client.close()


@pytest.mark.parametrize("action", ["reply", "exchange"])
def test_friend_detail_without_unique_match_is_contact_not_found(action):
	"""friend_detail 成功但没有唯一匹配：CONTACT_NOT_FOUND，浏览器动作不执行。"""
	client = _client(FakeHttpxClient([FakeResponse(payload=FRIEND_DETAIL_EMPTY)]))
	client._get_browser = MagicMock()  # type: ignore[method-assign]

	result = _invoke(client, action)

	assert result["code"] == -1
	assert result["__cli_error_code__"] == "CONTACT_NOT_FOUND"
	assert "friend_detail" in result["message"]
	assert BossRecruiterPlatform(client).parse_error(result)[0] == "CONTACT_NOT_FOUND"
	assert result["zpData"]["friendId"] == FRIEND_ID
	client._get_browser.assert_not_called()
	client.close()


def test_friend_detail_risk_still_stops_before_business_codes():
	"""风控优先级不变：friend_detail 返回风险码仍先抛 PlatformRiskError。"""
	from boss_agent_cli.api.client import AccountRiskError

	client = _client(FakeHttpxClient([FakeResponse(payload={"code": 36, "message": "账户异常"})]))
	client._get_browser = MagicMock()  # type: ignore[method-assign]

	with pytest.raises(AccountRiskError):
		client.send_message_by_friend(FRIEND_ID, "你好")
	client._get_browser.assert_not_called()
	client.close()


def test_send_message_by_friend_success_path_unchanged():
	"""唯一匹配时照旧走 geekClick + 发送链路，返回 code=0。"""
	client = _client(FakeHttpxClient([FakeResponse(payload=FRIEND_DETAIL_OK)]))
	client.chat_history = MagicMock(  # type: ignore[method-assign]
		side_effect=[
			{"code": 0, "zpData": {"messages": []}},
			{
				"code": 0,
				"zpData": {
					"messages": [{"mid": 7, "from": {"uid": 9}, "to": {"uid": FRIEND_ID}, "body": {"text": "你好"}}]
				},
			},
		]
	)
	browser = MagicMock()
	browser.evaluate_js_with_chat_events.return_value = {
		"value": {"ok": True, "log": ["geekClick called", "sendText returned undefined"]},
		"events": [{"kind": "ws_send", "bytes": 194, "utf8_bits": ["你好"]}],
	}
	client._get_browser = MagicMock(return_value=browser)  # type: ignore[method-assign]

	result = client.send_message_by_friend(FRIEND_ID, "你好")

	assert result["code"] == 0
	js_arg = browser.evaluate_js_with_chat_events.call_args[0][1]
	assert js_arg["friendData"]["friendId"] == FRIEND_ID
	assert js_arg["content"] == "你好"
	client.close()


# ── 命令层信封 ───────────────────────────────────────────────────


def _platform(friend_detail_response: dict) -> BossRecruiterPlatform:
	client = BossRecruiterClient(FakeAuth())
	client.friend_detail = MagicMock(return_value=friend_detail_response)  # type: ignore[method-assign]
	client._get_browser = MagicMock()  # type: ignore[method-assign]
	return BossRecruiterPlatform(client)


def _invoke_cli(tmp_path, argv: list[str], platform: BossRecruiterPlatform):
	with (
		patch("boss_agent_cli.commands.recruiter.reply.AuthManager"),
		patch("boss_agent_cli.commands.recruiter.reply.get_recruiter_platform_instance", return_value=platform),
		patch("boss_agent_cli.commands.recruiter.request_resume.AuthManager"),
		patch(
			"boss_agent_cli.commands.recruiter.request_resume.get_recruiter_platform_instance", return_value=platform
		),
		patch("boss_agent_cli.commands.recruiter.resume.AuthManager"),
		patch("boss_agent_cli.commands.recruiter.resume.get_recruiter_platform_instance", return_value=platform),
	):
		return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", *argv])


@pytest.mark.parametrize(
	("argv", "expected_code"),
	[
		(["hr", "reply", str(FRIEND_ID), "你好"], "AUTH_EXPIRED"),
		(["hr", "reply", str(FRIEND_ID), "你好"], "RATE_LIMITED"),
		(["hr", "reply", str(FRIEND_ID), "你好"], "TOKEN_REFRESH_FAILED"),
	],
	ids=["401-auth-expired", "9-rate-limited", "37-token-refresh-failed"],
)
def test_cli_reply_maps_friend_detail_platform_codes(tmp_path, argv, expected_code):
	responses = {
		"AUTH_EXPIRED": {"code": 401, "message": "平台认证已失效，请重新登录", "zpData": {}},
		"RATE_LIMITED": {"code": 9, "message": "请求频率过高", "zpData": {}},
		"TOKEN_REFRESH_FAILED": {"code": 37, "message": "stoken 已过期，请重新登录", "zpData": {}},
	}
	result = _invoke_cli(tmp_path, argv, _platform(responses[expected_code]))

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == expected_code
	if expected_code == "AUTH_EXPIRED":
		login_cmd = f"boss --data-dir {tmp_path.resolve()} login"
		assert login_cmd in payload["hints"]["next_actions"]


@pytest.mark.parametrize(
	"argv",
	[
		["hr", "reply", str(FRIEND_ID), "你好"],
		["hr", "request-resume", str(FRIEND_ID)],
		["hr", "resume", "--friend-id", str(FRIEND_ID), "--exchange"],
	],
	ids=["reply", "request-resume", "exchange"],
)
def test_cli_contact_not_found_points_to_hr_chat(tmp_path, argv):
	result = _invoke_cli(tmp_path, argv, _platform(FRIEND_DETAIL_EMPTY))

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	chat_cmd = f"boss --data-dir {tmp_path.resolve()} hr chat"
	assert payload["error"]["code"] == "CONTACT_NOT_FOUND"
	assert payload["error"]["recoverable"] is False
	assert payload["hints"]["next_actions"] == [chat_cmd]
	assert chat_cmd in payload["error"]["recovery_action"]
