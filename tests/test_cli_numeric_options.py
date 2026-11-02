"""页码 / 条数类 CLI 选项必须在解析阶段拒绝非法取值。

这些选项原先只有 `type=int`（或由 `default=1` 让 Click 推断成 int），写得下
`--page 0` / `--page -1` / `--count 0`，随后原样发给平台或当成切片边界：
`search --page -1` 发出 `page=-1` 的搜索请求，`chat --days -1` 把 cutoff 算到
未来（只返回空结果），`chatmsg --count 0` 请求 0 条消息。现在统一用
`click.IntRange` 在 Click 解析阶段拦下，由 `BossCliGroup` 转成 `INVALID_PARAM` 信封；
非数字取值（`--page abc`）原本就会被 Click 拒绝，这里一并锁住。
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli


@pytest.mark.parametrize(
	"argv",
	[
		["--json", "search", "golang", "--page", "abc"],
		["--json", "search", "golang", "--page", "0"],
		["--json", "recommend", "--page", "-1"],
		["--json", "chatmsg", "sec_1", "--page", "0"],
		["--json", "chatmsg", "sec_1", "--count", "0"],
		["--json", "chat-summary", "sec_1", "--page", "0"],
		["--json", "chat-summary", "sec_1", "--count", "-3"],
		["--json", "chat", "--page", "0"],
		["--json", "chat", "--days", "-1"],
		["--json", "history", "--page", "-1"],
		["--json", "hr", "applications", "--page", "0"],
		["--json", "hr", "candidates", "--page", "-2"],
		["--json", "hr", "chat", "--page", "0"],
		["--json", "hr", "chatmsg", "1", "--count", "-5"],
		["--json", "hr", "last-messages", "--page", "0"],
		["--json", "digest", "--days-stale", "-1"],
		["--json", "pipeline", "--days-stale", "-1"],
		["--json", "follow-up", "--days-stale", "-1"],
		["--json", "stats", "--days", "-1"],
	],
)
def test_numeric_options_reject_out_of_range_values(tmp_path, argv):
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), *argv])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"


@pytest.mark.parametrize(
	"argv",
	[
		["--json", "search", "golang", "--page", "2"],
		["--json", "chat", "--days", "0"],
		["--json", "digest", "--days-stale", "0"],
		["--json", "stats", "--days", "0"],
		["--json", "hr", "chatmsg", "1", "--count", "1"],
	],
)
def test_numeric_options_accept_valid_values(tmp_path, argv):
	"""合法值不能被误伤（`--days 0` 表示只看今天，语义上仍然成立）。"""
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), *argv])

	# 这里不关心请求是否成功（离线环境没有登录态），只要求不是参数校验失败。
	payload = json.loads(result.output)
	if payload["ok"] is False:
		assert payload["error"]["code"] != "INVALID_PARAM", payload["error"]
