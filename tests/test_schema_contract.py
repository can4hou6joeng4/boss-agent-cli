"""Schema 合约测试 + 错误码一致性测试。

确保 `boss schema` 输出的命令列表与 main.py 中注册的命令完全一致，
并验证代码中使用的每个错误码都在 schema 中声明。
"""

import ast
import json
import re
from pathlib import Path

from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.schema.data import SCHEMA_DATA


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


# ── 双受众信封契约 ──────────────────────────────────────────────────


def test_schema_conventions_declare_hints_audiences():
	"""conventions 必须声明 next_actions / operator_actions 各自的受众。"""
	hints = SCHEMA_DATA["conventions"]["hints"]
	assert set(hints) == {"next_actions", "operator_actions"}
	assert "Agent" in hints["next_actions"]
	assert "操作者" in hints["operator_actions"]


def test_schema_conventions_declare_command_vs_wizard():
	"""commands 与 wizard_catalog 是两套并行能力面，必须声明何时用哪个。"""
	declaration = SCHEMA_DATA["conventions"]["command_vs_wizard"]
	assert "顶层命令" in declaration
	assert "wizard" in declaration


# ── Schema 命令集 == 注册命令集 ─────────────────────────────────────


def test_schema_commands_match_registered():
	"""schema 中声明的命令集合应等于 main.py 中注册的所有顶层命令。"""
	# 从 CLI group 获取已注册命令名
	registered = set(cli.commands.keys())

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


def test_doctor_schema_documents_cdp_risk_boundary():
	"""doctor schema 只描述保留的 CDP 通道，不提供风控绕过入口。"""
	description = SCHEMA_DATA["commands"]["doctor"]["description"]
	assert "CDP" in description
	assert "Bridge" not in description
	assert "浏览器桥" not in description
	assert "不得用于规避平台风控" in description


def test_browser_source_schema_only_describes_supported_channels():
	description = SCHEMA_DATA["global_options"]["--browser-source"]["description"]
	assert "Bridge" not in description
	assert "CDP" in description
	assert "headless" in description
	assert "BROWSER_SESSION_NOT_FOUND" in description
	assert "CDP_UNAVAILABLE" in description


# ── 错误码一致性 ────────────────────────────────────────────────────

_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "boss_agent_cli"
_ERROR_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")

#: 源码中形如错误码、但不属于 CLI 信封错误码的全大写字面量。
#: 新增环境变量名等会触发扫描失败；确认不是错误码后加到这里。
_NON_ERROR_CODE_LITERALS = {
	"ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY",  # 代理环境变量名
	"BOSS_AGENT_MACHINE_ID", "BOSS_BROWSER_VERBOSE", "BOSS_DATA_DIR", "BOSS_EGRESS_PROBE_URL",  # 环境变量名
	"CLI_ERROR",  # MCP 层「CLI 输出不可解析」分类，不进入 CLI 信封
	"CREATE_NO_WINDOW",  # Windows 子进程常量
	"DRY_RUN",  # 招聘自动化事件状态，不是错误码
	"HOOK_SCRIPT_NAMES",  # crawler 常量名
	"PYTEST_CURRENT_TEST",  # pytest 环境变量
	"SERVER_INSTRUCTIONS",  # MCP server 常量名
	"UID_OR_SECURITY_ID",  # click metavar
}


def _literal_error_codes() -> set[str]:
	"""收集源码中形如错误码的字符串字面量（错误码定义文件自身除外）。"""
	codes: set[str] = set()
	for path in sorted(_SOURCE_ROOT.rglob("*.py")):
		if path == _SOURCE_ROOT / "schema" / "error_codes.py":
			continue
		tree = ast.parse(path.read_text(encoding="utf-8"))
		for node in ast.walk(tree):
			if (
				isinstance(node, ast.Constant)
				and isinstance(node.value, str)
				and _ERROR_CODE_PATTERN.match(node.value)
			):
				codes.add(node.value)
	return codes


def test_schema_error_codes_cover_all_used_codes():
	"""源码中出现的错误码字面量应全部在 schema error_codes 中声明。

	扫描源码而非维护手工清单：任何新增错误码只要以字面量出现在生产代码里，
	这里就会失败，避免 HOOK_BLOCKED / MODEL_SOURCE_NOT_FOUND 那类漏登记复发。
	"""
	schema_codes = set(SCHEMA_DATA["error_codes"].keys())
	used_codes = _literal_error_codes() - _NON_ERROR_CODE_LITERALS

	missing = used_codes - schema_codes
	assert not missing, (
		f"以下错误码在代码中使用但未在 schema 中声明: {sorted(missing)}"
	)

	# 豁免项若同时是已登记错误码，说明真实错误码被悄悄跳过
	overlap = _NON_ERROR_CODE_LITERALS & schema_codes
	assert not overlap, (
		f"以下豁免项已登记为错误码，不应出现在 _NON_ERROR_CODE_LITERALS: {sorted(overlap)}"
	)

	# 扫描器有效性自检：这些码必须以字面量出现在生产代码中
	assert {"INVALID_PARAM", "HOOK_BLOCKED", "MODEL_SOURCE_NOT_FOUND"} <= used_codes


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


def test_schema_error_codes_have_recovery_action_field():
	"""schema 中每个错误码都应显式声明 recovery_action 字段。"""
	error_codes = SCHEMA_DATA["error_codes"]
	missing = []
	for code, spec in error_codes.items():
		if "recovery_action" not in spec:
			missing.append(code)

	assert not missing, (
		f"以下错误码缺少 recovery_action 字段: {missing}"
	)


def test_search_schema_keeps_welfare_option():
	"""--welfare 是核心差异化能力，必须保留在 search schema 中。"""
	welfare = SCHEMA_DATA["commands"]["search"]["options"]["--welfare"]
	assert welfare["type"] == "string"
	assert "福利筛选" in welfare["description"]


def test_schema_exposes_recruiter_chat_context_commands():
	"""招聘者沟通上下文命令必须进入 boss schema 能力真源。"""
	subcommands = SCHEMA_DATA["commands"]["hr"]["subcommands"]
	assert "chatmsg" in subcommands
	assert "last-messages" in subcommands


def test_schema_environment_risk_is_terminal():
	"""ENVIRONMENT_RISK 必须是不可恢复的终止分支，恢复动作指向停止自动化。"""
	spec = SCHEMA_DATA["error_codes"]["ENVIRONMENT_RISK"]
	assert spec["recoverable"] is False
	assert spec["recovery_action"] == "停止自动化访问；保留原会话，在官方目标业务页确认风险解除后，由用户执行 boss clean --risk-lock 并显式恢复任务，降低访问频率"
	assert spec["message"] == "访问环境存在异常"


def test_schema_documents_browser_source_flag():
	"""--browser-source 必须进入 schema global_options，choices 覆盖全部来源且标 experimental。"""
	flag = SCHEMA_DATA["global_options"]["--browser-source"]
	assert sorted(flag["choices"]) == ["auto", "existing-browser", "httpx-only", "stored-cookie"]
	# choices 只从策略表推导（顺序亦同）：策略表加一行，schema / CLI / MCP 三处同时看到，不会漂移
	from boss_agent_cli.api.browser_source import POLICIES

	assert flag["choices"] == list(POLICIES)
	assert "BROWSER_SESSION_NOT_FOUND" in flag["description"]
	assert "CDP_UNAVAILABLE" in flag["description"]
	assert "httpx-only" in flag["description"]
	assert "BROWSER_DISABLED" in flag["description"]
	assert flag["default"] == "auto"
	assert flag["stability"] == "experimental"


def test_industry_choices_derive_from_lookup_table():
	"""--industry 的 choices 直接取自码表：码表增删一行，schema 与 CLI 的 Choice 同时看到。"""
	from boss_agent_cli.api.endpoints import INDUSTRY_CODES

	commands = SCHEMA_DATA["commands"]
	for name in ("search", "batch-greet"):
		assert commands[name]["options"]["--industry"]["choices"] == list(INDUSTRY_CODES), name
