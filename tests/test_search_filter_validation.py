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


def test_watch_add_rejects_unknown_filter_values(tmp_path):
	"""watch 保存的就是之后每次 run 的筛选条件，未知取值不能先存下来再静默丢弃。"""
	payload = _invoke(tmp_path, ["watch", "add", "w1", "golang", "--salary", "一百块"])

	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "未知薪资范围" in payload["error"]["message"]
