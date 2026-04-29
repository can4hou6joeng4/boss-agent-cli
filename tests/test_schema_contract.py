"""Schema 合约测试 + 错误码一致性测试。

确保 `boss schema` 输出的命令列表与 main.py 中注册的命令完全一致，
并验证代码中使用的每个错误码都在 schema 中声明。
"""

import json

from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.commands.schema import SCHEMA_DATA


# ── Schema 输出格式 ─────────────────────────────────────────────────


def test_schema_output_is_json_envelope():
	"""schema 输出应为标准 JSON 信封格式，包含必要顶层字段。"""
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	assert result.exit_code == 0

	parsed = json.loads(result.output)

	# 信封顶层字段
	assert parsed["ok"] is True
	assert parsed["schema_version"] == "1.0"
	assert parsed["command"] == "schema"
	assert parsed["error"] is None

	# data 子结构
	data = parsed["data"]
	assert "commands" in data
	assert "error_codes" in data
	assert "conventions" in data
	assert "global_options" in data
	assert isinstance(data["commands"], dict)
	assert isinstance(data["error_codes"], dict)


# ── Schema 命令集 == 注册命令集 ─────────────────────────────────────


def test_schema_commands_match_registered():
	"""schema 中声明的命令集合应等于 main.py 中注册的所有命令（排除 schema 自身）。"""
	# 从 CLI group 获取已注册命令名
	registered = set(cli.commands.keys()) - {"schema"}

	# 从 schema 获取声明命令名
	schema_commands = set(SCHEMA_DATA["commands"].keys())

	missing_in_schema = registered - schema_commands
	extra_in_schema = schema_commands - registered

	assert not missing_in_schema, (
		f"以下命令已注册但未在 schema 中声明: {missing_in_schema}"
	)
	assert not extra_in_schema, (
		f"以下命令在 schema 中声明但未注册: {extra_in_schema}"
	)


# ── 每个命令都有描述 ────────────────────────────────────────────────


def test_schema_commands_have_descriptions():
	"""schema 中每个命令都应有非空的 description 字段。"""
	commands = SCHEMA_DATA["commands"]
	missing = []
	for name, spec in commands.items():
		desc = spec.get("description", "")
		if not desc or not desc.strip():
			missing.append(name)

	assert not missing, (
		f"以下命令缺少 description: {missing}"
	)


# ── 错误码一致性 ────────────────────────────────────────────────────


def test_schema_error_codes_cover_all_used_codes():
	"""代码中实际使用的错误码应全部在 schema error_codes 中声明。

	已知例外:
	- HOOK_BLOCKED: 仅在启用钩子系统时出现，属于扩展机制，不在公开 schema 中声明。
	"""
	schema_codes = set(SCHEMA_DATA["error_codes"].keys())

	# 代码中实际使用的所有错误码（通过审计收集）
	used_codes = {
		"AUTH_EXPIRED",
		"AUTH_REQUIRED",
		"RATE_LIMITED",
		"TOKEN_REFRESH_FAILED",
		"ACCOUNT_RISK",
		"JOB_NOT_FOUND",
		"ALREADY_GREETED",
		"ALREADY_APPLIED",
		"GREET_LIMIT",
		"NETWORK_ERROR",
		"INVALID_PARAM",
		"NOT_SUPPORTED",
		"HOOK_BLOCKED",
	}

	# HOOK_BLOCKED 是内部扩展机制，允许不在公开 schema 中
	allowed_internal = {"HOOK_BLOCKED"}
	must_be_in_schema = used_codes - allowed_internal

	missing = must_be_in_schema - schema_codes
	assert not missing, (
		f"以下错误码在代码中使用但未在 schema 中声明: {missing}"
	)


def test_schema_error_codes_all_have_message():
	"""schema 中每个错误码都应有非空的 message 字段。"""
	error_codes = SCHEMA_DATA["error_codes"]
	missing = []
	for code, spec in error_codes.items():
		msg = spec.get("message", "")
		if not msg or not msg.strip():
			missing.append(code)

	assert not missing, (
		f"以下错误码缺少 message: {missing}"
	)


def test_schema_error_codes_have_recoverable_field():
	"""schema 中每个错误码都应声明 recoverable 字段。"""
	error_codes = SCHEMA_DATA["error_codes"]
	missing = []
	for code, spec in error_codes.items():
		if "recoverable" not in spec:
			missing.append(code)

	assert not missing, (
		f"以下错误码缺少 recoverable 字段: {missing}"
	)
