"""平台业务码 38 的风控映射与浏览器通道容器漂移（Issue #2045–#2046）。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from boss_agent_cli import display
from boss_agent_cli.api.browser_client import BrowserSession
from boss_agent_cli.api.client import AccountRiskError
from boss_agent_cli.api.endpoints_loader import EndpointSpec
from boss_agent_cli.api.zhipin_errors import raise_platform_risk_response
from boss_agent_cli.platforms.zhipin import BossPlatform
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform


# ── Issue #2045：38（身份资料流程）是账号侧风控，不得降级成可重试错误 ──


@pytest.mark.parametrize("code", [38, "38"])
def test_code_38_raises_account_risk(code: object) -> None:
	with pytest.raises(AccountRiskError):
		raise_platform_risk_response({"code": code, "message": "身份资料流程"})


@pytest.mark.parametrize("code", [38, "38"])
def test_parse_error_maps_38_to_account_risk(code: object) -> None:
	platform = BossPlatform(MagicMock())

	assert platform.parse_error({"code": code, "message": "身份资料流程"})[0] == "ACCOUNT_RISK"


@pytest.mark.parametrize("code", [38, "38"])
def test_recruiter_parse_error_maps_38_to_account_risk(code: object) -> None:
	platform = BossRecruiterPlatform(MagicMock())

	assert platform.parse_error({"code": code, "message": "身份资料流程"})[0] == "ACCOUNT_RISK"


def test_read_only_command_does_not_downgrade_account_risk() -> None:
	# display 只把 UNKNOWN 归一成 NETWORK_ERROR；38 必须在更早的分类里成为
	# ACCOUNT_RISK，否则 search 等只读命令会被提示「可重试」并继续打平台。
	platform = BossPlatform(MagicMock())
	code, _ = platform.parse_error({"code": 38, "message": "身份资料流程"})

	assert display.read_only_platform_code("search", code) == "ACCOUNT_RISK"


# ── Issue #2046：浏览器通道的 JSON.parse 顶层值可能不是对象 ──────────


class _Throttle:
	def wait(self) -> None:
		pass

	def mark(self) -> None:
		pass


def _session(monkeypatch) -> BrowserSession:
	session = BrowserSession({}, "ua")
	session._throttle = _Throttle()  # type: ignore[assignment]
	monkeypatch.setattr(session, "_check_request", lambda: None)
	return session


def test_send_fetch_normalizes_non_object_json_for_writes(monkeypatch) -> None:
	session = _session(monkeypatch)
	monkeypatch.setattr(session, "_evaluate_fetch", lambda payload: ["not-a-dict"])

	result = session._send_fetch({}, None)

	assert result["__cli_error_code__"] == "ACTION_UNCONFIRMED"
	details = result["__cli_error_details__"]
	assert details["outcome"] == "unknown"
	assert details["replay_safe"] is False


def test_send_fetch_normalizes_non_object_json_for_reads(monkeypatch) -> None:
	session = _session(monkeypatch)
	monkeypatch.setattr(session, "_evaluate_fetch", lambda payload: "ok")
	endpoint = EndpointSpec(
		name="read",
		method="GET",
		url="https://www.zhipin.com/wapi/example",
		referer="",
		effect="read",
		replay_safe=True,
	)

	result = session._send_fetch({}, endpoint)

	assert result["__cli_error_code__"] == "NETWORK_ERROR"
	details = result["__cli_error_details__"]
	assert details["outcome"] == "unknown"
	assert details["replay_safe"] is True
