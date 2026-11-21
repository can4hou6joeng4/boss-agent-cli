"""审计批次五十七：login 错误分类的风控优先级与限流关键词收窄（Issue #2144、#2145）。

- `"too many"` 子串会把本地 fd 耗尽的 strerror "Too many open files" 误判成
  不可恢复的平台风控（Issue #2144）。
- 风控判定排在 CDP / ConnectionError 分支之后，同时含 chrome/cdp 文本的风控
  异常被报成可重试的 CDP_UNAVAILABLE 并建议重新登录（Issue #2145）。
"""

from __future__ import annotations

import click

from boss_agent_cli.commands._login_errors import classify_login_error


def _classify(exc: Exception, tmp_path) -> dict:
	ctx = click.Context(click.Command("login"), obj={"platform": "zhipin", "data_dir": tmp_path})
	return classify_login_error(exc, ctx)


def test_emfile_is_not_classified_as_platform_risk(tmp_path):
	"""本地 fd 耗尽不是平台风控，不能报成不可恢复硬边界（Issue #2144）。"""
	payload = _classify(OSError(24, "Too many open files"), tmp_path)

	assert payload["code"] != "LOGIN_RISK_CONTROL"
	assert payload["code"] in ("NETWORK_ERROR", "LOCAL_IO_ERROR")
	assert "风控" not in str(payload["message"])
	assert not any(
		"平台客服" in action for action in payload["hints"]["operator_actions"]  # type: ignore[index]
	)


def test_rate_limit_text_is_classified_as_rate_limited(tmp_path):
	"""限流不是风控：文本兜底与 typed PlatformRateLimitError 同口径（Issue #2309）。"""
	payload = _classify(RuntimeError("429 Too many requests"), tmp_path)

	assert payload["code"] == "RATE_LIMITED"
	assert payload["recoverable"] is True


def test_cdp_text_does_not_mask_risk_control(tmp_path):
	"""CDP 异常文案同时含风控词时必须走硬边界（Issue #2145）。"""
	payload = _classify(RuntimeError("CDP 请求返回 403 forbidden"), tmp_path)

	assert payload["code"] == "LOGIN_RISK_CONTROL"
	assert payload["recoverable"] is False
	assert payload["hints"]["next_actions"] == []  # type: ignore[index]


def test_connection_error_with_risk_text_is_risk_control(tmp_path):
	"""ConnectionError 类型判定不能绕过风控文案（Issue #2145）。"""
	payload = _classify(ConnectionError("Chrome 触发风控"), tmp_path)

	assert payload["code"] == "LOGIN_RISK_CONTROL"
	assert payload["recoverable"] is False
	assert payload["hints"]["next_actions"] == []  # type: ignore[index]


def test_plain_cdp_connection_error_stays_cdp_unavailable(tmp_path):
	"""回归：不含风控词的 CDP 失败仍报可重试的 CDP_UNAVAILABLE。"""
	payload = _classify(ConnectionError("cdp refused"), tmp_path)

	assert payload["code"] == "CDP_UNAVAILABLE"
	assert payload["recoverable"] is True
