"""本轮窄范围审计确认的边界缺陷：出口探测、代理、stoken 注入、平台错误文案。

- Issue #2278：出口探测的客户端构造失败逃出 `unknown` 契约；
- Issue #2279：伪装成已知时区前缀的名称被当作地区一致；
- Issue #2280：Accept-Language 的数字地区子标签被误判为出口矛盾；
- Issue #2281：代理 host 的反斜杠与百分号编码让出口与校验值不一致；
- Issue #2282：stoken 注入假定 params 一定是可变映射；
- Issue #2283：平台结构化字段被 str() 后带进 error.message；
- Issue #2284：风控页判定的字段缺失 / 漂移导致探测链路崩溃；
- Issue #2285：AI 服务的 InvalidURL 分支漏掉本地参数错误契约。
"""

import httpx
import pytest
from unittest.mock import patch

from boss_agent_cli.ai.service import AIService, AIServiceError, recovery_action_for
from boss_agent_cli.api.egress import evaluate_egress, language_region, probe_egress, timezone_region
from boss_agent_cli.api.httpx_helpers import add_stoken_to_get_params
from boss_agent_cli.api.proxy import ProxyConfigurationError, parse_proxy_url
from boss_agent_cli.api.zhipin_errors import (
	is_security_verification_response,
	platform_risk_page_response,
	response_message,
)
from boss_agent_cli.platforms.base import classify_platform_error


# ── #2278：探测构造期失败 ──────────────────────────────────────────


def test_probe_returns_unknown_when_proxy_environment_is_malformed(monkeypatch):
	# 缺右括号的 IPv6 让 httpx 在构造客户端时抛 InvalidURL；探测必须自己吞掉。
	monkeypatch.setenv("ALL_PROXY", "http://[::1")
	environ = {
		"BOSS_EGRESS_PROBE_URL": "http://127.0.0.1:9/probe",
		"ALL_PROXY": "http://[::1",
	}

	verdict = probe_egress(environ=environ)

	assert verdict.status == "unknown"
	assert verdict.exit_country == ""
	assert "InvalidURL" in verdict.detail


# ── #2279 / #2280：地区判定 ───────────────────────────────────────


def test_spoofed_timezone_prefix_is_not_a_known_region():
	assert timezone_region("Asia/ShanghaiBogus") == ""
	verdict = evaluate_egress(exit_country="CN", accept_language="zh-CN", timezone="Asia/ShanghaiBogus")
	assert verdict.status == "unknown"


def test_real_timezones_still_map_to_regions():
	assert timezone_region("Asia/Shanghai") == "CN"
	assert timezone_region("America/New_York") == "US"
	assert timezone_region("America/Argentina/Buenos_Aires") == ""


@pytest.mark.parametrize(("accept_language", "region"), [("es-419", "419"), ("en-001", "001")])
def test_numeric_region_subtags_are_not_compared_with_country_codes(accept_language, region):
	# M.49 数字地区（拉美 / 世界）是国家码的超集，保留原值但不参与矛盾判定：
	# 拉美西语会话从美国出口启动是正常组合（Issue #2280）。
	assert language_region(accept_language) == region
	verdict = evaluate_egress(exit_country="US", accept_language=accept_language, timezone="America/New_York")
	assert verdict.status == "ok"


def test_letter_region_still_contradicts():
	verdict = evaluate_egress(exit_country="US", accept_language="ja-JP", timezone="America/New_York")

	assert verdict.status == "mismatch"


def test_letter_regions_and_scripts_still_work():
	assert language_region("zh-CN,zh;q=0.9") == "CN"
	assert language_region("sr-Latn-RS") == "RS"


# ── #2281：代理 host ───────────────────────────────────────────────


@pytest.mark.parametrize("raw", ["http://ho\\st:8080", "http://ho%20st:8080", "http://ho|st:8080"])
def test_proxy_host_characters_that_diverge_in_chromium_are_rejected(raw):
	with pytest.raises(ProxyConfigurationError):
		parse_proxy_url(raw, env_key="HTTPS_PROXY")


def test_ordinary_proxy_hosts_still_parse():
	assert parse_proxy_url("http://127.0.0.1:7890") == {"server": "http://127.0.0.1:7890"}
	assert parse_proxy_url("socks5://[::1]:1080") == {"server": "socks5://[::1]:1080"}


# ── #2282：stoken 注入 ────────────────────────────────────────────


def test_stoken_injection_creates_params_when_absent():
	kwargs = {}
	add_stoken_to_get_params("GET", kwargs, "token-1")
	assert kwargs["params"] == {"__zp_stoken__": "token-1"}


def test_stoken_injection_keeps_existing_mapping():
	kwargs = {"params": {"page": 1}}
	add_stoken_to_get_params("GET", kwargs, "token-1")
	assert kwargs["params"] == {"page": 1, "__zp_stoken__": "token-1"}


def test_stoken_injection_appends_to_pair_list_without_duplicates():
	kwargs = {"params": [("page", 1), ("__zp_stoken__", "stale")]}
	add_stoken_to_get_params("GET", kwargs, "token-1")
	assert kwargs["params"] == [("page", 1), ("__zp_stoken__", "token-1")]


def test_stoken_injection_also_accepts_pair_tuples():
	kwargs = {"params": (("page", 1),)}
	add_stoken_to_get_params("GET", kwargs, "token-1")
	assert kwargs["params"] == [("page", 1), ("__zp_stoken__", "token-1")]


@pytest.mark.parametrize("params", ["page=1", b"page=1"])
def test_stoken_injection_leaves_encoded_params_alone(params):
	kwargs = {"params": params}
	add_stoken_to_get_params("GET", kwargs, "token-1")
	assert kwargs["params"] == params


def test_stoken_injection_still_skips_non_get_requests():
	kwargs = {"params": None}
	add_stoken_to_get_params("POST", kwargs, "token-1")
	assert kwargs["params"] is None


# ── #2283：平台结构化字段不进入错误文案 ───────────────────────────


def test_structured_message_payloads_never_reach_error_text():
	assert response_message({"code": 36, "message": {"token": "SECRET"}}) == ""
	assert response_message({"code": 37, "zpData": {"token": "SECRET"}}) == ""
	assert "SECRET" not in response_message({"code": 36, "message": {"token": "SECRET"}})


def test_first_string_field_wins_over_later_structured_ones():
	payload = {"message": {"token": "SECRET"}, "msg": "请求失败"}
	assert response_message(payload) == "请求失败"


def test_empty_string_falls_through_to_the_next_field():
	assert response_message({"message": "", "error": "boom"}) == "boom"


def test_non_object_response_has_no_message():
	assert response_message(["boom"]) == ""  # type: ignore[arg-type]


def test_platform_error_classification_ignores_structured_messages():
	code, message = classify_platform_error({"code": 429, "message": {"token": "SECRET"}})
	assert code == "RATE_LIMITED"
	assert message == ""


# ── #2284：风控页判定的类型漂移 ───────────────────────────────────


@pytest.mark.parametrize("value", [None, 0, {}])
def test_risk_page_detection_tolerates_missing_fields(value):
	assert platform_risk_page_response(url=value, content_type=value, body=value) is None
	assert is_security_verification_response(url=value, content_type=value, body=value) is False


def test_risk_page_detection_still_reads_real_pages():
	page = (
		"<!doctype html><html><head><title>安全验证</title></head><body></body></html>"
	)
	response = platform_risk_page_response(content_type="text/html", body=page)
	assert response is not None
	assert response["__cli_risk_source__"] == "security_page"


# ── #2285：AI 服务的 InvalidURL 分类 ──────────────────────────────


@patch("boss_agent_cli.ai.service.httpx.post")
def test_invalid_url_is_unrecoverable_invalid_param(mock_post):
	mock_post.side_effect = httpx.InvalidURL("Invalid port: 'abc'")
	service = AIService(base_url="http://127.0.0.1:abc/v1", api_key="k", model="m")

	with pytest.raises(AIServiceError) as exc_info:
		service.chat([{"role": "user", "content": "hi"}])

	error = exc_info.value
	assert error.code == "INVALID_PARAM"
	assert error.recoverable is False
	assert "地址无效" in str(error)
	assert "--base-url" in recovery_action_for(error, "调用方默认建议")
