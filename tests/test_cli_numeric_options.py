"""页码 / 条数 / 标签 / 游标类 CLI 选项必须在解析阶段拒绝非法取值。

这些选项原先只有 `type=int`（或由 `default=1` 让 Click 推断成 int），写得下
`--page 0` / `--page -1` / `--count 0`，随后原样发给平台或当成切片边界：
`search --page -1` 发出 `page=-1` 的搜索请求，`chat --days -1` 把 cutoff 算到
未来（只返回空结果），`chatmsg --count 0` 请求 0 条消息，`me --deliver-page -3`
把负页码发给投递记录接口，`hr chatmsg --max-msg-id 0` 在客户端被 `if max_msg_id:`
静默丢弃（翻页变成重取最新一页）。现在统一用 `click.IntRange` / `click.FloatRange`
在 Click 解析阶段拦下，由 `BossCliGroup` 转成 `INVALID_PARAM` 信封；
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
		["--json", "me", "--deliver-page", "0"],
		["--json", "me", "--deliver-page", "-3"],
		["--json", "hr", "applications", "--label-id", "-1"],
		["--json", "hr", "chat", "--label-id", "-1"],
		["--json", "hr", "last-messages", "--label-id", "-1"],
		["--json", "hr", "chatmsg", "1", "--max-msg-id", "0"],
		["--json", "hr", "chatmsg", "1", "--max-msg-id", "-5"],
		["--json", "ai", "interview-prep", "jd", "--count", "0"],
		["--json", "ai", "interview-prep", "jd", "--count", "-2"],
		["--json", "ai", "config", "--max-tokens", "-1"],
		["--json", "ai", "config", "--temperature", "-0.5"],
		["--json", "hr", "chatmsg", "0"],
		["--json", "hr", "chatmsg", "-1"],
		["--json", "hr", "reply", "-1", "hi"],
		["--json", "hr", "request-resume", "0"],
		["--json", "hr", "last-messages", "--friend-id", "0"],
		["--json", "hr", "resume", "--exchange", "--friend-id", "-2"],
		["--json", "digest", "--days-stale", "-1"],
		["--json", "pipeline", "--days-stale", "-1"],
		["--json", "follow-up", "--days-stale", "-1"],
		["--json", "stats", "--days", "-1"],
		["--json", "login", "--timeout", "0"],
		["--json", "login", "--timeout", "-5"],
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
		["--json", "hr", "applications", "--label-id", "0"],
		["--json", "hr", "chatmsg", "1", "--max-msg-id", "100"],
		["--json", "ai", "config", "--temperature", "0"],
		["--json", "ai", "config", "--max-tokens", "1"],
		["--json", "hr", "chatmsg", "1"],
		["--json", "hr", "last-messages", "--friend-id", "1"],
	],
)
def test_numeric_options_accept_valid_values(tmp_path, argv):
	"""合法值不能被误伤（`--days 0` 表示只看今天，语义上仍然成立）。"""
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), *argv])

	# 这里不关心请求是否成功（离线环境没有登录态），只要求不是参数校验失败。
	payload = json.loads(result.output)
	if payload["ok"] is False:
		assert payload["error"]["code"] != "INVALID_PARAM", payload["error"]

def test_login_timeout_rejects_non_positive_values_before_login(tmp_path, monkeypatch):
	"""`login --timeout 0/-5` 原先会直接进入登录流程：`for i in range(timeout)`
	一次都不循环，以「扫码登录超时（0秒）」收尾（负数原样进文案），而不是参数错误。
	"""
	from boss_agent_cli.commands import login as login_module

	constructed = []
	monkeypatch.setattr(login_module, "AuthManager", lambda *a, **k: constructed.append(1))

	for value in ("0", "-5"):
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "login", "--timeout", value])
		assert result.exit_code == 1, result.output
		assert json.loads(result.output)["error"]["code"] == "INVALID_PARAM"

	assert constructed == []


def test_ai_config_invalid_values_are_not_persisted(tmp_path):
	"""`ai config --max-tokens -1` / `--temperature -0.5` 原先会被写进本机配置。

	取值在解析阶段就被拒掉，坏值不会落盘；否则之后每次 AI 调用都把非法参数
	发给服务商，失败信息来自网络层，看不出根源是本机配置。
	"""
	runner = CliRunner()
	for argv in (
		["ai", "config", "--max-tokens", "-1"],
		["ai", "config", "--temperature", "-0.5"],
	):
		result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", *argv])
		assert result.exit_code == 1, result.output
		assert json.loads(result.output)["error"]["code"] == "INVALID_PARAM"

	from boss_agent_cli.ai.config import AIConfigStore

	config = AIConfigStore(tmp_path).load_config()
	assert config["ai_max_tokens"] == 4096
	assert config["ai_temperature"] == 0.7
