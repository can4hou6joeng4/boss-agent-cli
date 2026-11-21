"""批次 103：写请求不跟随重定向（Issue #2424）与招聘者端字符串业务码 37（Issue #2425）。"""

from __future__ import annotations

import httpx
import pytest

from boss_agent_cli.api import endpoints
from boss_agent_cli.api.client import BossClient
from boss_agent_cli.api.recruiter_client import BossRecruiterClient


class _Auth:
	data_dir = None
	_platform = "zhipin"

	def get_token(self) -> dict[str, object]:
		return {"cookies": {}, "stoken": "s", "user_agent": "UA"}

	def force_refresh(self, **kwargs: object) -> None:
		pass


def _client_with(handler) -> tuple[BossClient, httpx.Client]:
	client = BossClient(_Auth(), delay=(0, 0))  # type: ignore[arg-type]
	client._throttle.wait = lambda: None  # type: ignore[method-assign]
	client._throttle.mark = lambda: None  # type: ignore[method-assign]
	http_client = httpx.Client(
		transport=httpx.MockTransport(handler),
		base_url="https://www.zhipin.com",
		follow_redirects=True,
	)
	client._get_client = lambda: http_client  # type: ignore[method-assign]
	return client, http_client


# ── Issue #2424：写请求首跳 3xx 按结果未知收口，不跟随 ────────────────


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_write_request_redirect_is_unconfirmed(status: int) -> None:
	seen: list[str] = []

	def handler(request: httpx.Request) -> httpx.Response:
		seen.append(str(request.url))
		return httpx.Response(status, headers={"Location": "https://www.zhipin.com/landing"})

	client, http_client = _client_with(handler)
	try:
		response = client.friend_label("fid", 1)
	finally:
		http_client.close()

	# 只发出一跳：httpx 不再跟随，307/308 也就不会让中间层重发 body。
	assert len(seen) == 1
	assert response["__cli_error_code__"] == "ACTION_UNCONFIRMED"
	assert response["__cli_error_details__"] == {
		"outcome": "unknown",
		"replay_safe": False,
		"reason": "redirect",
	}


def test_write_request_caller_cannot_re_enable_redirect_following() -> None:
	"""调用方显式传 follow_redirects=True 也按写请求从严关闭。"""
	seen: list[str] = []

	def handler(request: httpx.Request) -> httpx.Response:
		seen.append(str(request.url))
		return httpx.Response(302, headers={"Location": "https://www.zhipin.com/landing"})

	client, http_client = _client_with(handler)
	try:
		response = client._request(
			"GET", endpoints.FRIEND_LABEL_ADD_URL, params={"friendId": "fid"}, follow_redirects=True
		)
	finally:
		http_client.close()

	assert len(seen) == 1
	assert response["__cli_error_details__"]["reason"] == "redirect"


def test_read_request_still_follows_redirects() -> None:
	seen: list[str] = []

	def handler(request: httpx.Request) -> httpx.Response:
		seen.append(str(request.url))
		if len(seen) == 1:
			return httpx.Response(302, headers={"Location": "https://www.zhipin.com/wapi/zpuser/other/info"})
		return httpx.Response(200, json={"code": 0, "zpData": {"name": "x"}})

	client, http_client = _client_with(handler)
	try:
		response = client.user_info()
	finally:
		http_client.close()

	assert len(seen) == 2
	assert response["code"] == 0


# ── Issue #2425：招聘者端 stoken 过期判断兼容字符串 "37" ─────────────


def _recruiter() -> BossRecruiterClient:
	client = BossRecruiterClient(_Auth())  # type: ignore[arg-type]
	client._throttle.wait = lambda: None  # type: ignore[method-assign]
	client._throttle.mark = lambda: None  # type: ignore[method-assign]
	return client


@pytest.mark.parametrize("code", [37, "37"])
def test_recruiter_refresh_accepts_string_and_int_code(code: object) -> None:
	assert _recruiter()._should_refresh_token_response({"code": code, "message": "stoken 已过期"}) is True


@pytest.mark.parametrize("data", [{}, {"code": "0", "message": "ok"}, {"code": None}, {"code": "37", "message": "请求环境异常"}])
def test_recruiter_refresh_rejects_unrelated_responses(data: dict[str, object]) -> None:
	assert _recruiter()._should_refresh_token_response(dict(data)) is False
