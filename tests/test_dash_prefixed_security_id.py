"""security_id 可以合法以 '-' 开头，不能被 Click 当成未知选项。"""

import json

from click.testing import CliRunner

from boss_agent_cli.main import _declare_dash_prefixed_positionals, cli

# 实测形态：'F-' 之外的 base64url 取值，长度 328，首字符为 '-'
# 刻意用重复填充：只要首字符是 "-" 且够长就足以覆盖 Click 的解析分支，
# 不需要（也不应该）在这里放真实的 security_id 取值。
_SECURITY_ID = "-" + "a1B2c3D4e5" * 32


def _envelope(result) -> dict:
	return json.loads(result.output.splitlines()[-1])


def test_helper_inserts_separator_before_dash_prefixed_security_id() -> None:
	args = ["detail", _SECURITY_ID]

	assert _declare_dash_prefixed_positionals(cli, None, args) == ["detail", "--", _SECURITY_ID]


def test_helper_keeps_declared_options_before_the_separator() -> None:
	args = ["detail", "--lid", "L1", _SECURITY_ID]

	assert _declare_dash_prefixed_positionals(cli, None, args) == [
		"detail", "--lid", "L1", "--", _SECURITY_ID
	]


def test_helper_is_idempotent_when_separator_already_present() -> None:
	args = ["detail", "--", _SECURITY_ID]

	assert _declare_dash_prefixed_positionals(cli, None, args) == args


def test_helper_ignores_short_dash_values_that_are_not_security_ids() -> None:
	for args in (["search", "-x"], ["detail", "-E4"], ["greet", "-abc"]):
		assert _declare_dash_prefixed_positionals(cli, None, list(args)) == args


def test_helper_leaves_command_free_invocations_alone() -> None:
	assert _declare_dash_prefixed_positionals(cli, None, ["--version"]) == ["--version"]
	assert _declare_dash_prefixed_positionals(cli, None, []) == []


def test_helper_finds_command_after_global_options_with_values() -> None:
	"""全局选项的取值不能被子命令定位当成命令名（Issue #1447）。"""
	assert _declare_dash_prefixed_positionals(cli, None, ["--data-dir", "/tmp/d", "detail", _SECURITY_ID]) == [
		"--data-dir",
		"/tmp/d",
		"detail",
		"--",
		_SECURITY_ID,
	]
	assert _declare_dash_prefixed_positionals(cli, None, ["--data-dir=/tmp/d", "detail", _SECURITY_ID]) == [
		"--data-dir=/tmp/d",
		"detail",
		"--",
		_SECURITY_ID,
	]


def test_helper_keeps_options_after_a_dash_prefixed_positional() -> None:
	"""位置参数在前、选项在后时，选项不能被补出的分隔符吞掉（Issue #1448）。"""
	assert _declare_dash_prefixed_positionals(cli, None, ["detail", _SECURITY_ID, "--job-id", "J1"]) == [
		"detail",
		"--job-id",
		"J1",
		"--",
		_SECURITY_ID,
	]
	assert _declare_dash_prefixed_positionals(cli, None, ["mark", _SECURITY_ID, "--label", "收藏"]) == [
		"mark",
		"--label",
		"收藏",
		"--",
		_SECURITY_ID,
	]


def test_helper_does_not_rewrite_option_values_that_look_like_security_ids() -> None:
	"""选项取值即使以 - 开头并命中 security_id 形态，也必须留在选项后面（Issue #1449）。"""
	for args in (
		["detail", "sid-ok", "--job-id", _SECURITY_ID],
		["hr", "resume", "geek1", "--job-id", "J1", "--security-id", _SECURITY_ID],
		["hr", "greet", "--geek-id", "g1", "--security-id", _SECURITY_ID, "--message", "hi"],
		["export", "-o", "-2024-report-final-version"],
		["shortlist", "annotate", "sec", "job", "--note", "-follow-up-with-recruiter"],
	):
		assert _declare_dash_prefixed_positionals(cli, None, list(args)) == args


def test_helper_uses_leaf_command_options_inside_groups() -> None:
	assert _declare_dash_prefixed_positionals(cli, None, ["hr", "resume", _SECURITY_ID, "--job-id", "J1"]) == [
		"hr",
		"resume",
		"--job-id",
		"J1",
		"--",
		_SECURITY_ID,
	]


def test_cli_no_longer_rejects_dash_prefixed_security_id_as_an_option() -> None:
	"""(改前: INVALID_PARAM "No such option: -E")"""
	result = CliRunner().invoke(cli, ["detail", _SECURITY_ID])

	assert _envelope(result)["error"]["code"] != "INVALID_PARAM"


def test_cli_accepts_options_after_a_dash_prefixed_security_id(tmp_path) -> None:
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "detail", _SECURITY_ID, "--job-id", "J1"])

	assert "No such option" not in result.output
	assert _envelope(result)["error"]["code"] != "INVALID_PARAM"


def test_cli_finds_command_after_global_option_values(tmp_path) -> None:
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "detail", _SECURITY_ID])

	assert "No such option" not in result.output
	assert _envelope(result)["error"]["code"] != "INVALID_PARAM"


def test_cli_keeps_dash_prefixed_option_values_verbatim(tmp_path) -> None:
	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "detail", "sid-ok", "--job-id", _SECURITY_ID]
	)

	assert "No such option" not in result.output
	assert _envelope(result)["error"]["code"] != "INVALID_PARAM"
