"""批次 106：平台兜底错误分类优先风控、英文 rate 词边界与数值转换边界（Issue #2430、#2431）。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from boss_agent_cli.platforms.base import classify_platform_error
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform, _flag_number


# ── Issue #2430：风控优先于 auth，且环境风险短语单独归类 ─────────────


@pytest.mark.parametrize(
	"message",
	[
		"请登录后完成安全验证",
		"当前登录状态异常，请完成安全验证",
		"account risk detected",
	],
)
def test_risk_wins_over_auth_keywords(message: str) -> None:
	code, _ = classify_platform_error({"code": 9999, "message": message})

	assert code in {"ACCOUNT_RISK", "ENVIRONMENT_RISK"}


@pytest.mark.parametrize(
	"message",
	["环境存在异常", "访问环境异常", "请求环境异常", "登录状态异常"],
)
def test_environment_risk_phrases_are_detected(message: str) -> None:
	code, _ = classify_platform_error({"code": 9999, "message": message})

	assert code == "ENVIRONMENT_RISK"


def test_plain_abnormal_network_message_stays_network_error() -> None:
	# 裸「异常」不参与风控判定：普通网络失败不能变成不可恢复的风险码。
	code, _ = classify_platform_error({"code": 9999, "message": "网络异常，请稍后重试"})

	assert code == "NETWORK_ERROR"


def test_rate_word_requires_word_boundary() -> None:
	code, _ = classify_platform_error({"code": 9999, "message": "failed to generate card"})

	assert code == "UNKNOWN"


def test_rate_limit_english_still_detected() -> None:
	code, _ = classify_platform_error({"code": "ERR", "message": "rate limit"})

	assert code == "RATE_LIMITED"


def test_auth_message_without_risk_words_stays_auth_expired() -> None:
	code, _ = classify_platform_error({"code": "ERR", "message": "token expired"})

	assert code == "AUTH_EXPIRED"


# ── Issue #2431：数值标记与数字字符串码的转换边界 ───────────────────


@pytest.mark.parametrize("value", ["--0", "--3", "---1"])
def test_flag_number_rejects_multiple_minus_signs(value: str) -> None:
	assert _flag_number(value) is None


@pytest.mark.parametrize(("value", "expected"), [("3", 3), ("-1", -1), (" 0 ", 0)])
def test_flag_number_keeps_valid_forms(value: str, expected: int) -> None:
	assert _flag_number(value) == expected


def _recruiter() -> BossRecruiterPlatform:
	return BossRecruiterPlatform(MagicMock())


@pytest.mark.parametrize("code", ["1" * 5000, "²"])
def test_recruiter_parse_error_tolerates_unconvertible_codes(code: str) -> None:
	# 超长数字串 / 非十进制 isdigit 字符：按未映射码兜底，不抛 ValueError。
	assert _recruiter().parse_error({"code": code})[0] == "UNKNOWN"


def test_recruiter_parse_error_still_normalizes_numeric_string_codes() -> None:
	assert _recruiter().parse_error({"code": "9"})[0] == "RATE_LIMITED"


def test_recruiter_parse_error_handles_greet_flags_drifted_to_double_minus() -> None:
	from boss_agent_cli.api.recruiter_endpoints import BOSS_CHAT_START_URL

	platform = _recruiter()
	response = {
		"code": 0,
		"zpData": {"chat": "--0", "status": 3},
		"__cli_endpoint_hint__": BOSS_CHAT_START_URL,
	}

	# 开聊响应里的 "--0" 不再让 is_success / parse_error 抛 ValueError；
	# 无法确认是 0 标志时按普通成功返回，不把候选人记成权益拦截。
	assert platform.is_success(response) is True
	assert platform.parse_error({**response, "code": 9})[0] == "RATE_LIMITED"
