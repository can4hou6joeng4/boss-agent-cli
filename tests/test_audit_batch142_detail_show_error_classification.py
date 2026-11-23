"""审计批次 142：detail / show 的通道降级、错误分类与本地标识口径（Issue #2548 / #2549）。

- 参数类 4xx 与限流都不该换浏览器通道再发一次请求；
- httpx 卡片成功但载荷为空时，浏览器通道才可能拿到完整卡片；
- 本地缓存异常不能被当作「httpx 通道不可用」；
- 缓存里的不可编码 job_id 按未命中处理；
- 布尔 code 不是状态码，TTY 渲染与 raw_job_type 的字段漂移需要归一。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import httpx
from click.testing import CliRunner
import pytest

from boss_agent_cli.api._base_client import parameter_status_code
from boss_agent_cli.api.client import BossClient, PlatformRateLimitError
from boss_agent_cli.api.models import scalar_or_none
from boss_agent_cli.display import escape_markup
from boss_agent_cli.main import cli
from boss_agent_cli.platforms.zhipin import BossPlatform


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	return instance


def _status_error(status: int) -> httpx.HTTPStatusError:
	request = httpx.Request("GET", "https://www.zhipin.com/wapi/zpgeek/job/card.json")
	response = httpx.Response(status, request=request)
	return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


def _card_response() -> dict:
	return {
		"code": 0,
		"zpData": {
			"jobCard": {
				"encryptJobId": "j1",
				"jobName": "Go 开发",
				"brandName": "TestCo",
				"salaryDesc": "20K",
				"cityName": "广州",
				"experienceName": "3-5年",
				"degreeName": "本科",
				"address": "天河",
				"bossName": "王经理",
				"bossTitle": "HRBP",
			}
		},
	}


def _new_client() -> BossClient:
	"""跳过 __init__ 的纯逻辑实例：只测 job_card 的降级判定。"""
	client = object.__new__(BossClient)
	client.is_browser_only = Mock(return_value=False)
	client.browser_enabled = Mock(return_value=True)
	client._browser_request = Mock(return_value=_card_response())
	return client


# ── 参数类 4xx 判定 ─────────────────────────────────────────────────


@pytest.mark.parametrize("status", [400, 406, 409, 410, 422])
def test_parameter_status_code_detects_parameter_4xx(status: int) -> None:
	assert parameter_status_code(_status_error(status)) == status


@pytest.mark.parametrize("status", [401, 403, 404, 408, 429, 500])
def test_parameter_status_code_ignores_other_statuses(status: int) -> None:
	assert parameter_status_code(_status_error(status)) is None


def test_parameter_status_code_rejects_bool_and_missing_response() -> None:
	request = httpx.Request("GET", "https://www.zhipin.com/")
	response = httpx.Response(200, request=request)
	response.status_code = True  # 形状漂移：bool 不是状态码
	error = httpx.HTTPStatusError("drift", request=request, response=response)

	assert parameter_status_code(error) is None
	assert parameter_status_code(RuntimeError("no response")) is None


# ── job_card 通道选择 ───────────────────────────────────────────────


def test_job_card_does_not_fall_back_on_parameter_4xx() -> None:
	client = _new_client()
	client.job_card_httpx = Mock(side_effect=_status_error(400))

	with pytest.raises(httpx.HTTPStatusError):
		client.job_card("sid", "lid")

	client._browser_request.assert_not_called()


def test_job_card_does_not_fall_back_on_rate_limit() -> None:
	client = _new_client()
	client.job_card_httpx = Mock(side_effect=PlatformRateLimitError("limited"))

	with pytest.raises(PlatformRateLimitError):
		client.job_card("sid", "lid")

	client._browser_request.assert_not_called()


def test_job_card_falls_back_when_success_payload_is_empty() -> None:
	client = _new_client()
	client.job_card_httpx = Mock(return_value={"code": 0, "zpData": {}})

	result = client.job_card("sid", "lid")

	assert result == _card_response()
	client._browser_request.assert_called_once()


def test_job_card_returns_non_success_response_without_fallback() -> None:
	client = _new_client()
	auth_error = {"code": 401, "message": "平台认证已失效", "zpData": {}}
	client.job_card_httpx = Mock(return_value=auth_error)

	assert client.job_card("sid", "lid") == auth_error
	client._browser_request.assert_not_called()


def test_job_card_keeps_complete_success_payload() -> None:
	client = _new_client()
	client.job_card_httpx = Mock(return_value=_card_response())

	assert client.job_card("sid", "lid") == _card_response()
	client._browser_request.assert_not_called()


def test_job_card_falls_back_on_transport_error() -> None:
	client = _new_client()
	client.job_card_httpx = Mock(side_effect=httpx.ConnectError("boom"))

	assert client.job_card("sid", "lid") == _card_response()
	client._browser_request.assert_called_once()


# ── 平台错误分类与字段归一 ─────────────────────────────────────────


def test_bool_code_is_not_a_status_code() -> None:
	platform = object.__new__(BossPlatform)

	code, message = platform.parse_error({"code": False, "message": "oops"})

	assert code == "INTERNAL_ERROR"
	assert "布尔" in message


def test_integer_zero_still_wins_over_falsy() -> None:
	platform = object.__new__(BossPlatform)

	code, _ = platform.parse_error({"code": 9, "message": "slow down"})

	assert code == "RATE_LIMITED"


def test_scalar_or_none_drops_bool_but_keeps_numbers() -> None:
	assert scalar_or_none(True) is None
	assert scalar_or_none(False) is None
	assert scalar_or_none(0) == 0
	assert scalar_or_none("1") == "1"


def test_escape_markup_sanitizes_surrogates() -> None:
	rendered = escape_markup("工程师\ud800")

	rendered.encode("utf-8")
	assert "\\ud800" in rendered


# ── detail / show 命令边界 ──────────────────────────────────────────


def _detail_platform(*, job_detail=None, job_card=None):
	platform = MagicMock()
	platform.is_success.side_effect = lambda raw: raw.get("code") == 0
	platform.unwrap_data.side_effect = lambda raw: raw.get("zpData")
	platform.parse_error.side_effect = lambda raw: ("RATE_LIMITED", raw.get("message", ""))
	platform.job_detail.return_value = job_detail or {"code": 0, "zpData": {}}
	platform.job_card.return_value = job_card or _card_response()
	return platform


@patch("boss_agent_cli.commands.detail.CacheStore")
@patch("boss_agent_cli.commands.detail.AuthManager")
@patch("boss_agent_cli.commands.detail.get_platform_instance")
def test_detail_treats_undecodable_cached_job_id_as_miss(
	mock_platform_cls, mock_auth_cls, mock_cache_cls, tmp_path: Path
) -> None:
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.get_job_id.return_value = "j\ud800"
	mock_cache.is_greeted.return_value = False
	platform = _ctx_mock(mock_platform_cls)
	platform.is_success.side_effect = lambda raw: raw.get("code") == 0
	platform.unwrap_data.side_effect = lambda raw: raw.get("zpData")
	platform.job_card.return_value = _card_response()

	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "detail", "sid1"]
	)

	assert result.exit_code == 0, result.output
	platform.job_detail.assert_not_called()
	platform.job_card.assert_called_once()


@patch("boss_agent_cli.commands.detail.CacheStore")
@patch("boss_agent_cli.commands.detail.AuthManager")
@patch("boss_agent_cli.commands.detail.get_platform_instance")
def test_detail_does_not_fall_back_after_rate_limited_response(
	mock_platform_cls, mock_auth_cls, mock_cache_cls, tmp_path: Path
) -> None:
	_ctx_mock(mock_cache_cls)
	platform = _ctx_mock(mock_platform_cls)
	platform.is_success.return_value = False
	platform.parse_error.return_value = ("RATE_LIMITED", "慢一点")
	platform.job_detail.return_value = {"code": 9, "message": "慢一点"}

	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "detail", "sid1", "--job-id", "j1"]
	)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "RATE_LIMITED"
	assert payload["error"]["recoverable"] is True
	platform.job_card.assert_not_called()


@patch("boss_agent_cli.commands.detail.CacheStore")
@patch("boss_agent_cli.commands.detail.AuthManager")
@patch("boss_agent_cli.commands.detail.get_platform_instance")
def test_detail_browser_parameter_error_reports_invalid_param(
	mock_platform_cls, mock_auth_cls, mock_cache_cls, tmp_path: Path
) -> None:
	_ctx_mock(mock_cache_cls)
	platform = _ctx_mock(mock_platform_cls)
	platform.job_card.side_effect = _status_error(400)

	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "detail", "sid1"]
	)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert payload["error"]["recoverable"] is False


@patch("boss_agent_cli.commands.detail.CacheStore")
@patch("boss_agent_cli.commands.detail.AuthManager")
@patch("boss_agent_cli.commands.detail.get_platform_instance")
def test_detail_rate_limit_exception_reports_rate_limited(
	mock_platform_cls, mock_auth_cls, mock_cache_cls, tmp_path: Path
) -> None:
	_ctx_mock(mock_cache_cls)
	platform = _ctx_mock(mock_platform_cls)
	platform.job_card.side_effect = PlatformRateLimitError("limited")

	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "detail", "sid1"]
	)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "RATE_LIMITED"


@patch("boss_agent_cli.commands.show.CacheStore")
@patch("boss_agent_cli.commands.show.AuthManager")
@patch("boss_agent_cli.commands.show.get_platform_instance")
@patch("boss_agent_cli.commands.show.get_index_entry")
def test_show_parameter_error_reports_invalid_param(
	mock_get_entry, mock_platform_cls, mock_auth_cls, mock_cache_cls
) -> None:
	mock_get_entry.return_value = (
		{"exists": True, "source": "search:Go", "page": 1, "count": 1, "saved_at": 1700000000.0},
		{"security_id": "sec_001", "job_id": "j1"},
	)
	_ctx_mock(mock_cache_cls)
	platform = _ctx_mock(mock_platform_cls)
	platform.job_card.side_effect = _status_error(422)

	result = CliRunner().invoke(cli, ["--json", "show", "1"])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert payload["error"]["recoverable"] is False


# ── 向导动作同口径（Issue #2548） ──────────────────────────────────


def test_wizard_detail_parameter_error_becomes_invalid_param() -> None:
	from boss_agent_cli.wizard.actions import execute_candidate_detail
	from boss_agent_cli.wizard.runner import WorkflowActionError

	class _Platform:
		def job_card(self, security_id, lid=""):
			raise _status_error(400)

	with pytest.raises(WorkflowActionError) as excinfo:
		execute_candidate_detail(_Platform(), security_id="sec-1")

	assert excinfo.value.code == "INVALID_PARAM"
	assert excinfo.value.recoverable is False
	assert excinfo.value.auto_retry is False


def test_wizard_detail_rate_limit_exception_becomes_rate_limited() -> None:
	from boss_agent_cli.wizard.actions import execute_candidate_detail
	from boss_agent_cli.wizard.runner import WorkflowActionError

	class _Platform:
		def job_card(self, security_id, lid=""):
			raise PlatformRateLimitError("limited")

	with pytest.raises(WorkflowActionError) as excinfo:
		execute_candidate_detail(_Platform(), security_id="sec-1")

	assert excinfo.value.code == "RATE_LIMITED"
	assert excinfo.value.recoverable is True
