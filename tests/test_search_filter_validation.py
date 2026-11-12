"""搜索类命令的筛选参数必须在发请求之前校验。

`export` 和 `batch-greet` 以前把 `--city` 原样交给 `search_jobs`，未知城市会在
请求构造阶段抛 `ValueError`，被兜底成「可重试」的 `NETWORK_ERROR`——拿同一个
非法城市重试永远修不好。`batch-greet` 的 `--salary` / `--experience` /
`--education` 更糟：未知取值被静默丢弃，等于不筛选就直接打招呼（写操作）。
`export --count 0/-1` 则一行不导、`ok=true` 地宣告成功。
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli


def _invoke(tmp_path, argv: list[str]) -> dict:
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", *argv])
	assert result.exit_code == 1, result.output
	return json.loads(result.output)


@pytest.mark.parametrize(
	("argv", "target"),
	[
		(["export", "golang", "--city", "火星"], "boss_agent_cli.commands.export.get_platform_instance"),
		(["batch-greet", "golang", "--city", "火星"], "boss_agent_cli.commands.greet.get_platform_instance"),
		(
			["batch-greet", "golang", "--salary", "一百块"],
			"boss_agent_cli.commands.greet.get_platform_instance",
		),
		(
			["batch-greet", "golang", "--experience", "十年以上"],
			"boss_agent_cli.commands.greet.get_platform_instance",
		),
	],
)
def test_invalid_filters_are_param_errors_before_any_request(tmp_path, argv, target):
	with patch(target) as platform:
		payload = _invoke(tmp_path, argv)

	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM", payload["error"]
	assert "未知" in payload["error"]["message"]
	# 参数错误必须挡在平台请求之前，而不是发出去以后兜成网络错误。
	platform.assert_not_called()


@pytest.mark.parametrize("argv", [["export", "golang", "--count", "0"], ["export", "golang", "--count", "-1"]])
def test_export_count_must_be_positive(tmp_path, argv):
	payload = _invoke(tmp_path, argv)

	assert payload["error"]["code"] == "INVALID_PARAM"


@pytest.mark.parametrize(
	"argv",
	[
		["search", "golang", "--city", "火星"],
		["watch", "add", "w1", "golang", "--city", "火星"],
	],
)
def test_city_validation_still_reports_param_error(tmp_path, argv):
	payload = _invoke(tmp_path, argv)

	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "未知城市" in payload["error"]["message"]
	# 城市错误要指向完整城市列表，而不是让 Agent 照着正文再猜一次。
	assert payload["hints"]["next_actions"] == ["boss cities"]
	assert "boss cities" in payload["error"]["recovery_action"]


def test_watch_add_rejects_unknown_filter_values(tmp_path):
	"""watch 保存的就是之后每次 run 的筛选条件，未知取值不能先存下来再静默丢弃。"""
	payload = _invoke(tmp_path, ["watch", "add", "w1", "golang", "--salary", "一百块"])

	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "未知薪资范围" in payload["error"]["message"]


def test_unknown_filter_value_lists_choices_and_recovery_action(tmp_path):
	"""没有字面相近值时列出可选值：小表全列，大表给总数和样例。

	``未知薪资范围: 一百块`` 原来既不说有哪些合法取值，``recovery_action`` 也只是
	登记表里的「修正参数」，Agent 只能原样重试。
	"""
	payload = _invoke(tmp_path, ["watch", "add", "w1", "golang", "--salary", "一百块"])
	message = payload["error"]["message"]
	assert "可选值" in message
	assert "10-20K" in message
	assert "修正参数" not in payload["error"]["recovery_action"]
	assert "筛选项" in payload["error"]["recovery_action"]

	# search / export 的 --industry 支持逗号多选，不走 click.Choice，才轮到这里的提示。
	payload = _invoke(tmp_path, ["export", "golang", "--industry", "zzz"])
	message = payload["error"]["message"]
	assert "未知行业类型: zzz" in message
	assert "共 97 个可选值" in message
	assert "例如" in message


@pytest.mark.parametrize(
	("argv", "message"),
	[
		(["search", "--preset", ""], "--preset 的值不能为空"),
		(["search", "--preset", "   "], "--preset 的值不能为空"),
		(["search", "--url", ""], "--url 的值不能为空"),
		(["search", "--url", "   "], "--url 的值不能为空"),
		(["export", "--url", ""], "--url 的值不能为空"),
	],
)
def test_blank_selector_values_are_param_errors(tmp_path, argv, message):
	"""传了 flag 但值是空的时候不能回「未提供 query」。

	``boss search --preset ""`` 原来回「未提供 query，请传入搜索关键词、--preset
	或 --url」——用户明明传了 ``--preset``，读到的却是「你没传」；纯空白的
	``--url`` 更被当成域名不匹配。真正的原因是 flag 后面少了值。
	"""
	payload = _invoke(tmp_path, argv)

	assert payload["error"]["code"] == "INVALID_PARAM"
	assert payload["error"]["message"] == message
	assert payload["hints"]["next_actions"]


def test_search_without_any_selector_points_at_keyword_and_presets(tmp_path):
	"""什么都没传时给出可执行的下一步，而不是笼统的「修正参数」。"""
	payload = _invoke(tmp_path, ["search"])

	assert payload["error"]["code"] == "INVALID_PARAM"
	prefix = f"boss --data-dir {tmp_path.resolve()}"
	assert payload["hints"]["next_actions"] == [
		f"{prefix} search <关键词>",
		f"{prefix} preset list",
	]
	assert f"{prefix} search <关键词>" in payload["error"]["recovery_action"]


def test_search_url_parse_error_points_at_keyword_search(tmp_path):
	"""非 BOSS 直聘链接要给出可执行替代，而不是重试同一条链接。"""
	payload = _invoke(tmp_path, ["search", "--url", "https://example.com/web/geek/jobs?query=Python"])

	assert payload["error"]["code"] == "INVALID_PARAM"
	assert payload["hints"]["next_actions"] == [f"boss --data-dir {tmp_path.resolve()} search <关键词>"]
	assert "search <关键词>" in payload["error"]["recovery_action"]
