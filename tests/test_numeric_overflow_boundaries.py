"""本地数值转换溢出的边界收口（Issue #1230）。

`json.loads` 能正常解析 4300 位以内的大整数与 ``1e400``（→ ``inf``），但
``float()`` / ``int()`` / ``math.isfinite()`` 在溢出时抛 ``OverflowError``。
``BossCliGroup`` 把它归入 ``INTERNAL_ERROR``（指引「升级版本」），
``handle_auth_errors`` 把解析异常兜成可重试的 ``NETWORK_ERROR``——两类都是
确定性本地数据问题，重试/升级都无意义。这里按各模块既有语义断言兜底结果。
"""

import json

import pytest
from click.testing import CliRunner

from boss_agent_cli.ai.config import AIConfigStore, ai_float_setting
from boss_agent_cli.automation import reply_ai
from boss_agent_cli.automation.config import AutomationConfig, automation_config_from_dict
from boss_agent_cli.automation.models import CandidateKey, Conversation, Decision, PlatformAction
from boss_agent_cli.automation.reply_ai import parse_reply_draft
from boss_agent_cli.commands.agent import _fit_score
from boss_agent_cli.commands.search import _parse_cached_result
from boss_agent_cli.config import validate_request_config
from boss_agent_cli.crawler.service import crawl_cdp_port, crawl_int_setting
from boss_agent_cli.main import cli

_OVERLONG_INT = "9" * 5000
_DEEP = "[" * 100_000 + "]" * 100_000
_HUGE_INT = 10**400


def _envelope(result):
	payload = json.loads(result.output)
	assert payload["ok"] is False
	assert payload["error"]["code"]
	return payload


# ── 搜索缓存 ────────────────────────────────────────────────────────


def test_search_cache_parse_returns_miss_on_overlong_integer():
	assert _parse_cached_result('{"data": [], "pad": ' + _OVERLONG_INT + "}") is None


def test_search_cache_parse_never_raises_on_deep_nesting():
	# 3.11–3.13 解析深嵌套抛 RecursionError（按未命中处理）；3.14 起正常解析。
	result = _parse_cached_result('{"data": [], "pad": ' + _DEEP + "}")
	assert result is None or isinstance(result, dict)


# ── 启动期配置校验 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
	"cfg",
	[
		{"request_delay": [0, _HUGE_INT]},
		{"batch_greet_delay": [_HUGE_INT, _HUGE_INT]},
		{"request_wait_timeout": _HUGE_INT},
	],
)
def test_request_config_rejects_overflowing_integers(cfg):
	with pytest.raises(ValueError):
		validate_request_config(cfg)


def test_request_config_accepts_regular_values():
	validate_request_config({"request_delay": [0.5, 1.5], "request_wait_timeout": 30})


def test_startup_config_overflow_reports_invalid_param(tmp_path):
	(tmp_path / "config.json").write_text(
		'{"request_delay": [0, ' + str(_HUGE_INT) + "]}", encoding="utf-8"
	)

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "schema"])

	assert result.exit_code == 1
	payload = _envelope(result)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "request_delay" in payload["error"]["message"]


# ── automation / crawl 配置 ─────────────────────────────────────────


@pytest.mark.parametrize(
	"raw",
	[
		{"max_per_tab": float("inf")},
		{"human_review_threshold": _HUGE_INT},
		{"auto_execute_threshold": float("inf")},
	],
)
def test_automation_config_rejects_overflow(raw):
	with pytest.raises(ValueError):
		automation_config_from_dict(raw)


def test_config_set_automation_overflow_is_invalid_param(tmp_path):
	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "config", "set", "automation", '{"max_per_tab": 1e400}']
	)

	assert result.exit_code == 1
	assert _envelope(result)["error"]["code"] == "INVALID_PARAM"


def test_crawl_int_setting_rejects_infinite_value():
	with pytest.raises(ValueError):
		crawl_int_setting({"max_requests": float("inf")}, "max_requests", 10)


def test_crawl_cdp_port_rejects_infinite_value():
	with pytest.raises(ValueError):
		crawl_cdp_port(float("inf"))


def test_config_set_crawl_overflow_is_invalid_param(tmp_path):
	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "config", "set", "crawl", '{"max_requests": 1e400}']
	)

	assert result.exit_code == 1
	assert _envelope(result)["error"]["code"] == "INVALID_PARAM"


# ── AI 配置 ─────────────────────────────────────────────────────────


def test_ai_float_setting_rejects_overflowing_integer():
	with pytest.raises(ValueError):
		ai_float_setting({"ai_temperature": _HUGE_INT}, "ai_temperature", 0.7)


def test_ai_float_setting_keeps_regular_values():
	assert ai_float_setting({"ai_temperature": 0.7}, "ai_temperature", 0.1) == 0.7


# ── 本地模型输出 ────────────────────────────────────────────────────


def test_reply_draft_rejects_overflowing_confidence():
	payload = {
		"action": "send_questionnaire",
		"confidence": _HUGE_INT,
		"reply": "您好",
		"reason": "r",
		"risk_flags": [],
	}
	with pytest.raises(ValueError):
		parse_reply_draft(json.dumps(payload, ensure_ascii=False))


def _synthetic_decision() -> Decision:
	return Decision(
		action=PlatformAction.SEND_FOLLOW_UP,
		confidence=0.9,
		reason="synthetic",
		candidate_key=CandidateKey("synthetic"),
	)


@pytest.mark.parametrize(
	"raw",
	[
		json.dumps(
			{
				"action": "send_follow_up",
				"confidence": _HUGE_INT,
				"reply": "x",
				"reason": "y",
				"risk_flags": [],
			}
		),
		_DEEP,
	],
	ids=["huge-int-confidence", "deep-nesting"],
)
def test_local_reply_falls_back_when_model_output_overflows(tmp_path, monkeypatch, raw):
	store = AIConfigStore(tmp_path)
	store.save_api_key("synthetic-key")
	store.save_config(ai_provider="ollama", ai_model="synthetic-model")
	monkeypatch.setattr(reply_ai.AIService, "chat", lambda self, messages: raw)

	result = reply_ai._draft_with_local_ai(
		_synthetic_decision(), Conversation(title="synthetic"), AutomationConfig(), tmp_path
	)

	assert result.requires_human is True
	assert "local-ai-parse-error" in result.risk_flags


def test_fit_score_treats_infinite_score_as_zero():
	assert _fit_score({"match_score": float("inf")}) == 0


def test_fit_score_keeps_regular_scores():
	assert _fit_score({"match_score": 85}) == 85
