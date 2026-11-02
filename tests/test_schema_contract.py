"""Schema 合约测试 + 错误码一致性测试。

确保 `boss schema` 输出的命令列表与 main.py 中注册的命令完全一致，
并验证代码中使用的每个错误码都在 schema 中声明。
"""

import ast
import json
import re
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.schema.data import SCHEMA_DATA
from boss_agent_cli.schema.error_codes import ERROR_CODES


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


# ── 子命令声明 == 实际注册子命令 ─────────────────────────────────────

_SUBCOMMAND_LIST_PATTERN = re.compile(r"子命令：([^）)]+)")


def test_schema_subcommand_lists_match_click_tree():
	"""description 里「子命令：a/b/c」的声明必须与实际 Click 组一致。

	crawl 曾漏掉 shortlist：`boss schema` 是 Agent 的能力真源，声明不全
	等于把导入候选池的能力藏了起来。
	"""
	problems: list[str] = []
	for name, spec in SCHEMA_DATA["commands"].items():
		match = _SUBCOMMAND_LIST_PATTERN.search(str(spec.get("description", "")))
		if match is None:
			continue
		declared = sorted(item.strip() for item in match.group(1).split("/"))
		command = cli.commands.get(name)
		actual = sorted(getattr(command, "commands", {}) or {})
		if declared != actual:
			problems.append(f"{name}: 声明 {declared} != 实际 {actual}")

	assert not problems, "schema 子命令声明与实际注册不一致:\n" + "\n".join(problems)


def test_schema_hr_subcommands_match_click_tree():
	"""hr 的结构化 subcommands 必须是实际子命令的完整镜像。"""
	declared = set(SCHEMA_DATA["commands"]["hr"]["subcommands"])
	actual = set(cli.commands["hr"].commands)
	assert declared == actual, (
		f"hr subcommands 声明与实际不一致: 缺 {sorted(actual - declared)} 多 {sorted(declared - actual)}"
	)


# ── 选项声明 == 实际可执行选项 ───────────────────────────────────────


def _click_param_options(node: Any) -> set[str]:
	names: set[str] = set()
	for param in getattr(node, "params", ()):
		for opt in getattr(param, "opts", ()):
			names.add(opt)
			if "/" in opt:
				names.add("--" + opt.rsplit("/", 1)[-1].lstrip("-"))
	return names


def _schema_option_spellings(key: str) -> set[str]:
	"""把 `-o, --output` / `--output` 拆成 Click 使用的拼写形式。"""
	return {part.strip() for part in key.split(",") if part.strip().startswith("-")}


def _subcommand_option_names(node: Any) -> set[str]:
	names = _click_param_options(node)
	for sub in (getattr(node, "commands", {}) or {}).values():
		names |= _click_param_options(sub)
		names |= _subcommand_option_names(sub)
	return names


def test_schema_option_declarations_are_executable():
	"""schema 里声明的选项必须落在实际命令或子命令上。

	`agent` 曾把 `agent run` 的三个选项写成 `agent` 的命令级选项：Agent 按
	schema 构造 `boss agent --resume` 会直接被 Click 拒绝。命令级选项只能
	出现在命令自身，子命令选项必须放进分组。
	"""
	problems: list[str] = []
	for name, spec in SCHEMA_DATA["commands"].items():
		node = cli.commands.get(name)
		if node is None:
			continue
		for key, value in (spec.get("options") or {}).items():
			if key.startswith("-"):
				missing = _schema_option_spellings(key) - _click_param_options(node)
				if missing:
					problems.append(f"{name}: 命令级选项 {sorted(missing)} 不在命令自身参数中")
				continue
			if not isinstance(value, dict):
				continue
			sub = (getattr(node, "commands", {}) or {}).get(key)
			if sub is None:
				problems.append(f"{name}: 选项分组 {key!r} 不是已注册子命令")
				continue
			available = _subcommand_option_names(sub)
			for opt_key in value:
				missing = _schema_option_spellings(opt_key) - available
				if missing:
					problems.append(f"{name}.{key}: 选项 {sorted(missing)} 不在子命令参数中")
	assert not problems, "schema 声明了不可执行的选项:\n" + "\n".join(problems)


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


def test_cli_error_envelope_metadata_matches_registry():
	"""端到端：命令只传 code / message 时，信封元数据由登记表补齐。

	曾经的缺口：命令层大量 `INVALID_PARAM` 调用点不传 `recoverable` /
	`recovery_action`，信封里两者落成 `false` / `null`，Agent 拿不到
	`schema.error_codes` 中已经声明好的「修正参数」指引。
	"""
	result = CliRunner().invoke(cli, ["--json", "export", "--url", "https://example.com/not-boss"])
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert parsed["error"]["recoverable"] is ERROR_CODES["INVALID_PARAM"]["recoverable"]
	assert parsed["error"]["recovery_action"] == ERROR_CODES["INVALID_PARAM"]["recovery_action"]


# 恢复指引里的 `boss` 命令片段：只吃 ASCII 命令 token，遇到选项 / 占位符 /
# 中文自然语言即停止，避免把整句中文当成命令的一部分。
_BOSS_COMMAND_PATTERN = re.compile(r"\bboss((?: (?:--platform \S+|--json|[a-z][a-z0-9-]*))*)")
_BACKTICK_FRAGMENT_PATTERN = re.compile(r"`([^`]+)`")


def _resolve_command(tokens: list[str]) -> Any:
	"""按 Click 命令树解析 token，返回 (完整路径, 叶子命令, 未知 token)。"""
	node: Any = cli
	path: list[str] = []
	for token in tokens:
		if token == "--json":
			continue
		if token == "--platform":
			# 平台名之后无法继续按命令树校验，视为已解析。
			return path, node, None
		commands = getattr(node, "commands", None)
		if commands is None:
			return path, node, None
		sub = commands.get(token)
		if sub is None:
			return path, node, token
		node = sub
		path.append(token)
	return path, node, None


def _option_names(command: Any) -> set[str]:
	names: set[str] = set()
	for param in getattr(command, "params", ()):
		for opt in getattr(param, "opts", ()):
			names.add(opt)
			if "/" in opt:
				names.add("--" + opt.rsplit("/", 1)[-1].lstrip("-"))
	return names


def _recovery_command_problems() -> list[str]:
	problems: list[str] = []
	for code, spec in ERROR_CODES.items():
		action = str(spec.get("recovery_action") or "")
		for match in _BOSS_COMMAND_PATTERN.finditer(action):
			tokens = match.group(1).split()
			if tokens[:1] == ["--platform"]:
				tokens = tokens[2:]
			_, _, unknown = _resolve_command(tokens)
			if unknown is not None:
				problems.append(f"{code}: 未知命令 {unknown!r}（{action}）")
		# 选项只在反引号包裹的完整命令片段里校验：自然语言里裸写的 --xxx
		# 可能属于别的上下文，误报会逼着文案迁就测试。
		for fragment in _BACKTICK_FRAGMENT_PATTERN.findall(action):
			fragment = fragment.strip()
			if not fragment.startswith("boss "):
				continue
			tokens = fragment.split()[1:]
			if tokens[:1] == ["--platform"]:
				tokens = tokens[2:]
			_, leaf, unknown = _resolve_command(tokens)
			if unknown is not None:
				problems.append(f"{code}: 未知命令 {unknown!r}（{fragment}）")
				continue
			options = _option_names(leaf)
			for token in tokens:
				if token.startswith("--") and token not in options:
					problems.append(f"{code}: 未知选项 {token!r}（{fragment}）")
	return problems


def test_error_recovery_actions_reference_real_commands_and_options():
	"""恢复指引必须能被照做：引用的 boss 命令 / 选项都存在。

	#540 的两条无效指引（要求设置已无作用的 operating_mode、提示
	`uv run playwright install chromium` 而实际用 patchright）说明登记表
	文案会随命令演进而失效。这里把 `boss ...` 引用钉在 Click 命令树上。
	"""
	problems = _recovery_command_problems()
	assert not problems, "恢复指引引用了不存在或已改名的命令/选项:\n" + "\n".join(problems)
