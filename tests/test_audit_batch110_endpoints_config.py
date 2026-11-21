"""批次 110：API YAML 加载边界与响应码单一真源（Issue #2446–#2448）。"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from boss_agent_cli.api import endpoints, endpoints_loader, zhipin_errors
from boss_agent_cli.api.browser_client import BrowserSession
from boss_agent_cli.api.endpoints_loader import _parse_api_spec


def _raw(**overrides: Any) -> dict[str, Any]:
	raw: dict[str, Any] = {
		"base_url": "https://www.zhipin.com",
		"endpoints": {"search": {"path": "/wapi/zpgeek/search/joblist.json", "method": "POST"}},
		"response_codes": {"success": 0, "stoken_expired": 37},
	}
	raw.update(overrides)
	return raw


# ── Issue #2446：response_codes 归一与单一真源 ──────────────────────


def test_quoted_response_codes_are_coerced_to_int() -> None:
	spec = _parse_api_spec(_raw(response_codes={"success": "0", "stoken_expired": "37"}))

	assert spec.response_codes == {"success": 0, "stoken_expired": 37}
	for value in spec.response_codes.values():
		assert type(value) is int


@pytest.mark.parametrize(
	"value",
	[True, False, None, "37.0", "0x25", "", "9" * 5000, [37], {"v": 37}],
)
def test_non_integer_response_codes_are_rejected(value: object) -> None:
	with pytest.raises(ValueError, match=r"boss\.yaml: response_codes\.stoken_expired 必须是整数"):
		_parse_api_spec(_raw(response_codes={"stoken_expired": value}))


def test_normalize_risk_code_tracks_configured_stoken_expired() -> None:
	code = endpoints.CODE_STOKEN_EXPIRED

	assert zhipin_errors.normalize_risk_code(code) == code
	assert zhipin_errors.normalize_risk_code(str(code)) == code


@pytest.mark.parametrize(
	"code",
	[35, "35", 36, "36", 38, "38", endpoints.CODE_IP_RESTRICTED, endpoints.CODE_ACCOUNT_RESTRICTED],
)
def test_normalize_risk_code_keeps_observed_fixed_codes(code: int | str) -> None:
	assert zhipin_errors.normalize_risk_code(code) == int(code)


@pytest.mark.parametrize("value", [None, [], {}, True, False, 0, "0", "x" * 5000])
def test_normalize_risk_code_rejects_other_values(value: object) -> None:
	assert zhipin_errors.normalize_risk_code(value) is None


def test_browser_rate_limit_code_follows_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
	# 浏览器通道必须跟随 response_codes 配置，而不是硬编码 9（Issue #2446）。
	monkeypatch.setattr(endpoints, "CODE_RATE_LIMITED", 7)
	client = BrowserSession.__new__(BrowserSession)
	client._request_budget = None
	client._throttle = MagicMock()
	client._check_request = lambda: None
	client._evaluate_fetch = lambda payload: {"code": "7", "zpData": {}}

	result = client._send_fetch({}, None)

	assert result["code"] == 7


def test_browser_rate_limit_code_ignores_stale_literal(monkeypatch: pytest.MonkeyPatch) -> None:
	# 配置改成 7 后，旧字面量 9 不再被浏览器通道当成限流码。
	monkeypatch.setattr(endpoints, "CODE_RATE_LIMITED", 7)
	client = BrowserSession.__new__(BrowserSession)
	client._request_budget = None
	client._throttle = MagicMock()
	client._check_request = lambda: None
	client._evaluate_fetch = lambda payload: {"code": 9, "zpData": {}}

	result = client._send_fetch({}, None)

	assert result["code"] == 9


# ── Issue #2447：YAML 形状校验与加载错误上下文 ──────────────────────


@pytest.mark.parametrize(
	("raw", "message"),
	[
		([], "顶层 必须是映射"),
		({"base_url": "", "endpoints": {}}, "base_url 必须是非空字符串"),
		({"base_url": 1, "endpoints": {}}, "base_url 必须是非空字符串"),
		(_raw(endpoints=None), "endpoints 必须是映射"),
		(_raw(endpoints={"search": "https://example.test"}), "endpoints.search 必须是映射"),
		(
			_raw(endpoints={"search": {"url": "", "method": "POST"}}),
			"endpoints.search 的 url / path 至少需要一个非空字符串",
		),
		(
			_raw(endpoints={"search": {"url": 0, "path": "", "method": "POST"}}),
			"endpoints.search 的 url / path 至少需要一个非空字符串",
		),
		(_raw(endpoints={"search": {"path": "/x", "method": None}}), "endpoints.search.method 必须是非空字符串"),
		(_raw(endpoints={"search": {"path": "/x", "referer": None}}), "endpoints.search.referer 必须是非空字符串"),
		(_raw(web_pages=None), "web_pages 必须是映射"),
		(_raw(web_pages={"geek_base": None}), "web_pages.geek_base 必须是非空字符串"),
		(_raw(default_headers=None), "default_headers 必须是映射"),
		(_raw(default_headers={"Accept": None}), "default_headers.Accept 必须是非空字符串"),
		(_raw(response_codes=None), "response_codes 必须是映射"),
		(_raw(lookups=None), "lookups 必须是映射"),
		(_raw(lookups={"city": None}), "lookups.city 必须是映射"),
		(_raw(lookups={"city": {"北京": None}}), "lookups.city.北京 必须是非空字符串"),
	],
)
def test_malformed_spec_is_rejected_with_context(raw: Any, message: str) -> None:
	with pytest.raises(ValueError, match=re.escape(message)):
		_parse_api_spec(raw)


@pytest.mark.parametrize("raw", [None, "", [], 3])
def test_non_mapping_root_is_rejected(raw: object) -> None:
	with pytest.raises(ValueError, match="顶层 必须是映射"):
		_parse_api_spec(raw)  # type: ignore[arg-type]


def test_loader_errors_include_filename() -> None:
	with pytest.raises(ValueError, match=r"recruiter\.yaml: base_url"):
		_parse_api_spec({"base_url": 1}, "recruiter.yaml")


def test_missing_yaml_file_has_context() -> None:
	with pytest.raises(ValueError, match="no-such.yaml: 无法读取 API 配置"):
		endpoints_loader._load_yaml("no-such.yaml")


def test_yaml_parse_error_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
	class _Ref:
		def read_text(self, encoding: str) -> str:
			return "base_url: [unclosed"

	class _Files:
		def joinpath(self, name: str) -> _Ref:
			return _Ref()

	monkeypatch.setattr(endpoints_loader.importlib.resources, "files", lambda package: _Files())

	with pytest.raises(ValueError, match="bad.yaml: 无法解析 YAML"):
		endpoints_loader._load_yaml("bad.yaml")


def test_blank_endpoint_url_does_not_silently_resolve_to_home() -> None:
	# 旧实现把 url: "" 静默解析成 https://www.zhipin.com/（Issue #2447）。
	with pytest.raises(ValueError, match="endpoints.search 的 url / path"):
		_parse_api_spec(_raw(endpoints={"search": {"url": "", "path": "", "method": "POST"}}))


def test_missing_endpoint_constant_has_context(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setattr(endpoints, "_spec", SimpleNamespace(endpoints={}))

	with pytest.raises(ValueError, match="boss.yaml: endpoints 缺少 search"):
		endpoints._url("search")


# ── Issue #2448：单个 spec 损坏不打断另一侧的请求 ──────────────────


def test_endpoint_for_request_survives_broken_recruiter_spec(monkeypatch: pytest.MonkeyPatch) -> None:
	def _broken() -> Any:
		raise ValueError("recruiter.yaml: endpoints 必须是映射")

	monkeypatch.setattr(endpoints_loader, "get_recruiter_spec", _broken)

	resolved = endpoints_loader.endpoint_for_request("POST", endpoints.SEARCH_URL)

	assert resolved is not None
	assert resolved.name == "search"


def test_endpoint_for_request_returns_none_for_unknown_recruiter_url(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	def _broken() -> Any:
		raise ValueError("recruiter.yaml: endpoints 必须是映射")

	monkeypatch.setattr(endpoints_loader, "get_recruiter_spec", _broken)

	assert endpoints_loader.endpoint_for_request("POST", "https://www.zhipin.com/wapi/zpjob/recruiter/unknown") is None
