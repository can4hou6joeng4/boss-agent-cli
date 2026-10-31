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


def test_cli_no_longer_rejects_dash_prefixed_security_id_as_an_option() -> None:
	"""(改前: INVALID_PARAM "No such option: -E")"""
	result = CliRunner().invoke(cli, ["detail", _SECURITY_ID])

	assert _envelope(result)["error"]["code"] != "INVALID_PARAM"
