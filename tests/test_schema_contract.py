"""Schema 合约测试 + 错误码一致性测试。

确保 `boss schema` 输出的命令列表与 main.py 中注册的命令完全一致，
并验证代码中使用的每个错误码都在 schema 中声明。
"""

import ast
import json
import re
from pathlib import Path
from typing import Any

import click
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


def test_schema_options_and_args_have_descriptions():
	"""schema 声明的每个选项与位置参数都必须有非空 description。

	`crawl results --detail-status` 曾只声明 enum 不写说明：Agent 能从 schema
	枚举出 completed / pending 两个取值，却不知道它们分别指详情已抓取与待抓取。
	"""
	missing: list[str] = []

	def walk(path: str, spec: Any) -> None:
		if not isinstance(spec, dict):
			return
		for key, value in spec.items():
			if str(key).startswith("-") and isinstance(value, dict):
				if not str(value.get("description") or "").strip():
					missing.append(f"{path} {key}")
				continue
			if key == "args" and isinstance(value, list):
				for entry in value:
					if isinstance(entry, dict) and not str(entry.get("description") or "").strip():
						missing.append(f"{path} <{entry.get('name')}>")
				continue
			if key in ("description", "subcommands"):
				continue
			walk(f"{path}.{key}", value)

	for name, spec in SCHEMA_DATA["commands"].items():
		walk(name, spec)

	assert not missing, "以下选项/参数缺少 description: " + ", ".join(missing)


def _iter_schema_subcommands(entries: dict[str, Any], prefix: str):
	"""遍历 schema options 里的子命令条目，返回 ``(路径, 条目)``。

	两种形态并存：`watch` / `crawl` 的子命令条目带 ``args``/``options`` 包装，
	`ai.local` 则直接是「子命令名 → 声明」的字典。空对象是「无参数子命令」的
	占位（见 ``test_schema_options_declare_every_subcommand``），跳过。
	"""
	for name, spec in entries.items():
		if name.startswith("-") or not isinstance(spec, dict) or not spec:
			continue
		yield prefix + name, spec
		if "args" not in spec and "options" not in spec:
			yield from _iter_schema_subcommands(spec, prefix + name + " ")


def test_schema_subcommands_have_descriptions():
	"""schema 中每个子命令都应有非空的 description 字段。

	`boss schema` 是 Agent 的能力真源：子命令只列参数和选项、不写它做什么，
	Agent 就只知道 `watch add` 收 name + query，不知道它会保存下来供 `run`
	增量拉取。原先 43 个子命令缺描述。
	"""
	missing: list[str] = []
	for name, spec in SCHEMA_DATA["commands"].items():
		for path, entry in _iter_schema_subcommands(spec.get("options") or {}, f"{name} "):
			if not str(entry.get("description") or "").strip():
				missing.append(path)

	assert not missing, f"以下子命令缺少 description: {missing}"


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


def test_schema_struct_subcommands_match_click_tree():
	"""结构化 subcommands 的键集合必须与实际一级子命令一致。

	`boss schema` 是能力真源。`watch` / `preset` 原先没有 `subcommands`，
	`watch list` / `preset list` 在 options 里也缺席，Agent 据此枚举只能看到
	add/remove，列不出已保存的条件。agent 用平铺的「review list」形式描述
	嵌套子命令，不适用集合比较，跳过。
	"""
	problems: list[str] = []
	for name, spec in SCHEMA_DATA["commands"].items():
		declared = spec.get("subcommands")
		if not isinstance(declared, dict):
			continue
		if any(" " in str(key) for key in declared):
			continue
		command = cli.commands.get(name)
		if command is None:
			continue
		actual = set(getattr(command, "commands", {}) or {})
		if set(declared) != actual:
			problems.append(
				f"{name}: 声明 {sorted(declared)} != 实际 {sorted(actual)}"
			)

	assert not problems, "schema 结构化子命令声明与实际注册不一致:\n" + "\n".join(problems)


def test_schema_options_declare_every_subcommand():
	"""每个已注册子命令都必须在 options 里出现（无参数时用空对象占位）。

	Agent 通过 options 的非选项键枚举可执行子命令。`shortlist` / `config` /
	`resume` 的 list、`agent` 的 control/pending/stats、`ai local` 的
	status/smoke、`hr whoami` 原先只在 `subcommands` 描述里出现，遍历 options
	的调用方会漏掉这些能力。
	"""
	problems: list[str] = []

	def walk(path: str, node: Any, options: dict[str, Any]) -> None:
		for sub_name, sub_node in (getattr(node, "commands", {}) or {}).items():
			if sub_name not in options:
				problems.append(f"{path}.{sub_name}")
				continue
			sub_spec = options[sub_name]
			if isinstance(sub_spec, dict):
				walk(f"{path}.{sub_name}", sub_node, sub_spec)

	for name, spec in SCHEMA_DATA["commands"].items():
		node = cli.commands.get(name)
		options = spec.get("options")
		if node is not None and isinstance(options, dict):
			walk(name, node, options)

	assert not problems, "schema 未在 options 中声明子命令: " + ", ".join(problems)


# ── 选项声明 == 实际可执行选项 ───────────────────────────────────────


def _click_param_options(node: Any) -> set[str]:
	names: set[str] = set()
	for param in getattr(node, "params", ()):
		if isinstance(param, click.Argument):
			continue
		for opt in getattr(param, "opts", ()):
			names.add(opt)
			if "/" in opt:
				names.add("--" + opt.rsplit("/", 1)[-1].lstrip("-"))
		for opt in getattr(param, "secondary_opts", ()):
			names.add(opt)
	return names


def _schema_option_spellings(key: str) -> set[str]:
	"""把 `-o, --output` / `--output` 拆成 Click 使用的拼写形式。"""
	return {part.strip() for part in key.split(",") if part.strip().startswith("-")}


#: 刻意不进入 native schema 的可执行选项：内部隐藏 / 历史兼容 / 测试专用 / 与全局重复。
#: 键是 Click 命令路径（`命令.分组.子分组`），值是该路径下允许未声明的选项。
_UNDECLARED_SCHEMA_OPTIONS: dict[str, set[str]] = {
	"login": {"--ignore-risk-lock"},  # 历史兼容，已明确不再跳过本地风控锁
	"wizard": {"--json"},  # 与全局 --json 重复
	"pipeline": {"--now-ts-ms"},  # 测试专用时间戳覆盖
	"follow-up": {"--now-ts-ms"},
	"digest": {"--now-ts-ms"},
	"crawl.resume": {"--from-queue"},  # 后台恢复子进程内部使用，Click hidden
	"agent.crawl": {"--allow-crawl"},  # 历史兼容，当前版本无需传入
}


def test_schema_option_declarations_are_executable():
	"""schema 的 options 必须与 Click 命令树双向一致。

	声明的选项要真的落在对应命令/子命令上（Agent 照 schema 构造 argv 不能
	被 Click 拒绝）；可执行的公开选项也必须声明（否则 Agent 从 schema 里
	看不到能力）。分组按 CLI 子命令名递归，`hr jobs list --page` 这种深层
	选项要写进 `jobs.list`，不能平铺在 `jobs` 上。

	`agent` 曾把 `agent run` 的三个选项写成 `agent` 的命令级选项：Agent 按
	schema 构造 `boss agent --resume` 会直接被 Click 拒绝。
	"""
	problems: list[str] = []

	def walk(path: str, node: Any, group: dict[str, Any]) -> None:
		declared: set[str] = set()
		subgroups: dict[str, dict[str, Any]] = {}
		for key, value in group.items():
			if not isinstance(value, dict):
				continue
			if str(key).startswith("-"):
				declared |= _schema_option_spellings(str(key))
			else:
				subgroups[str(key)] = value

		actual = _click_param_options(node)
		allowed = _UNDECLARED_SCHEMA_OPTIONS.get(path, set())
		undeclared = actual - declared - allowed
		if undeclared:
			problems.append(f"{path}: 可执行但未声明 {sorted(undeclared)}")
		unexecutable = declared - actual
		if unexecutable:
			problems.append(f"{path}: 声明了不可执行 {sorted(unexecutable)}")

		subcommands = getattr(node, "commands", {}) or {}
		for key, value in subgroups.items():
			sub = subcommands.get(key)
			if sub is None:
				problems.append(f"{path}: 选项分组 {key!r} 不是已注册子命令")
				continue
			walk(f"{path}.{key}", sub, value)
		for sub_name, sub in subcommands.items():
			if sub_name not in subgroups:
				walk(f"{path}.{sub_name}", sub, {})

	for name, spec in SCHEMA_DATA["commands"].items():
		node = cli.commands.get(name)
		if node is not None:
			walk(name, node, spec.get("options") or {})

	# 豁免项若已经声明，说明豁免清单过期，应删掉而不是继续跳过
	for path, options in _UNDECLARED_SCHEMA_OPTIONS.items():
		group: Any = SCHEMA_DATA["commands"].get(path.split(".")[0], {}).get("options")
		for token in path.split(".")[1:]:
			group = group.get(token) if isinstance(group, dict) else None
		declared = {
			spelling
			for key in (group or {})
			if str(key).startswith("-")
			for spelling in _schema_option_spellings(str(key))
		}
		for option in sorted(options & declared):
			problems.append(f"豁免项 {path} {option} 已可声明，请从 _UNDECLARED_SCHEMA_OPTIONS 删除")

	assert not problems, "schema 选项声明与 Click 命令树不一致:\n" + "\n".join(problems)


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


# ── 位置参数声明 == 实际可执行参数 ───────────────────────────────────


def _click_param_args(node: Any) -> list[tuple[str, bool]]:
	return [(param.name, param.required) for param in getattr(node, "params", ()) if isinstance(param, click.Argument)]


def _schema_arg_entries(spec: dict[str, Any]) -> list[tuple[str, bool]]:
	return [
		(str(entry.get("name")), bool(entry.get("required")))
		for entry in spec.get("args", [])
		if isinstance(entry, dict)
	]


#: 刻意不进入 native schema 的位置参数（内部隐藏 / 测试专用）。
_UNDECLARED_SCHEMA_ARGS: dict[str, set[str]] = {}


def _schema_subcommand_specs(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
	"""返回某个规范节点下直接挂着的子命令分组。

	schema 有两种形状：命令级 `options` 下的非选项键（`crawl.run`、`watch.add`），
	以及不带 `options` 的分组里直接挂子命令（`hr.jobs`、`agent.review`、
	`ai.local`）。两种都要递归，否则深层位置参数会漏检。
	"""
	if isinstance(spec.get("options"), dict):
		return {
			str(key): value
			for key, value in spec["options"].items()
			if not str(key).startswith("-") and isinstance(value, dict)
		}
	reserved = {"description", "args", "options", "subcommands", "mcp_tools"}
	return {
		str(key): value
		for key, value in spec.items()
		if str(key) not in reserved and not str(key).startswith("-") and isinstance(value, dict)
	}


def test_schema_arg_declarations_are_executable():
	"""schema 的 args 必须与 Click 命令树双向一致。

	位置参数按声明顺序绑定，Agent 照 schema 构造 `boss watch add <name> <query>`
	时顺序或必填性错了都会被 Click 拒绝。声明的参数必须真的落在对应命令上；
	可执行的位置参数也必须声明；两者顺序与 required 都要一致。
	"""
	problems: list[str] = []

	def walk(path: str, node: Any, spec: dict[str, Any]) -> None:
		declared = _schema_arg_entries(spec)
		actual = _click_param_args(node)
		allowed = _UNDECLARED_SCHEMA_ARGS.get(path, set())

		declared_names = [name for name, _ in declared]
		actual_names = [name for name, _ in actual]
		if declared_names != actual_names:
			problems.append(f"{path}: schema args {declared_names} != click args {actual_names}")
		for name, required in declared:
			actual_required = dict(actual).get(name)
			if actual_required is not None and actual_required != required:
				problems.append(f"{path}: 参数 {name} required 声明为 {required}，实际为 {actual_required}")
		for name in actual_names:
			if name not in declared_names and name not in allowed:
				problems.append(f"{path}: 可执行但未声明位置参数 {name}")

		subcommands = getattr(node, "commands", {}) or {}
		children = _schema_subcommand_specs(spec)
		for key, value in children.items():
			sub = subcommands.get(key)
			if sub is None:
				continue  # 未注册分组由选项一致性测试报告
			walk(f"{path}.{key}", sub, value)
		for sub_name, sub in subcommands.items():
			if sub_name not in children:
				walk(f"{path}.{sub_name}", sub, {})

	for name, spec in SCHEMA_DATA["commands"].items():
		node = cli.commands.get(name)
		if node is not None:
			walk(name, node, spec)

	assert not problems, "schema 位置参数声明与 Click 命令树不一致:\n" + "\n".join(problems)


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
	"AUTO_EXECUTED", "DRY_RUN",  # 招聘自动化事件的成功状态，不是错误码
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


def _referenced_string_literals() -> set[str]:
	"""收集源码中的全部字符串字面量（错误码定义文件自身除外）。

	反向守卫不能用 `_literal_error_codes`：它的正则要求带下划线，像 `UNKNOWN`
	这样的单字码会被判成「从未引用」。这里只关心字符串是否出现，不判断形态；
	docstring 命中会漏报（当作已引用），不会误报。
	"""
	literals: set[str] = set()
	for path in sorted(_SOURCE_ROOT.rglob("*.py")):
		if path == _SOURCE_ROOT / "schema" / "error_codes.py":
			continue
		tree = ast.parse(path.read_text(encoding="utf-8"))
		for node in ast.walk(tree):
			if isinstance(node, ast.Constant) and isinstance(node.value, str):
				literals.add(node.value)
	return literals


def test_schema_error_codes_mark_never_emitted_codes():
	"""登记表里从不被源码引用的码必须标注「当前版本不主动产生」。

	`boss schema` 把 error_codes 整体暴露给 Agent；一个登记了却永远不会出现在信封里的
	码，会让 Agent 为不存在的失败分支准备恢复逻辑。RESUME_NOT_SHARED 与
	RECRUITER_NOT_AUTHORIZED 曾以普通可恢复码的措辞留在登记表里，却从未有生产路径
	产生它们。历史兼容码保留是明确的惯例（见 COMPLIANCE_BLOCKED 等），但必须显式标注。
	"""
	referenced = _referenced_string_literals()
	unmarked = sorted(
		code
		for code, spec in SCHEMA_DATA["error_codes"].items()
		if code not in referenced and "不主动产生" not in spec["message"]
	)

	assert not unmarked, (
		f"以下错误码从未在源码中出现，但 message 未标注「当前版本不主动产生」：{unmarked}"
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


def test_schema_error_codes_have_recovery_action_text():
	"""每个错误码都要给出可理解的 recovery_action，null 会让 Agent 停在原地。

	`APPLICATION_NOT_FOUND` / `JOB_POST_LIMIT` 曾以 `recovery_action: null` 登记，
	但它们从未被任何代码路径产生，schema 里却像是真实可恢复分支。
	"""
	missing = [
		code
		for code, spec in SCHEMA_DATA["error_codes"].items()
		if not isinstance(spec.get("recovery_action"), str) or not spec["recovery_action"].strip()
	]

	assert not missing, f"以下错误码缺少 recovery_action 文案: {missing}"


def test_schema_error_codes_exclude_success_event_statuses():
	"""AUTO_EXECUTED / DRY_RUN 是招聘自动化事件的成功状态，不能登记成错误码。

	`AUTO_EXECUTED` 曾以 `recoverable: false`、`recovery_action: null` 混进
	error_codes：Agent 枚举「可能收到的错误」时会看到一个表示「动作已执行」的条目。
	真正的失败状态（如 `CIRCUIT_BREAKER_OPEN`）仍保留在登记表里。
	"""
	from boss_agent_cli.automation.models import EventStatus

	for status in (EventStatus.AUTO_EXECUTED, EventStatus.DRY_RUN):
		assert status.value not in SCHEMA_DATA["error_codes"]


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

	这里用最小探针命令验证兜底路径本身；不绑定某个尚未补专属指引的真实
	命令，避免专用文案一补齐，这个契约测试就跟着红。
	"""
	from boss_agent_cli.display import handle_error_output

	@click.command("probe")
	@click.pass_context
	def probe(ctx: click.Context) -> None:
		handle_error_output(ctx, "probe", code="INVALID_PARAM", message="probe")

	result = CliRunner().invoke(probe, [])
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

def test_resume_schema_marks_uid_gid_deprecated():
	"""`--uid` / `--gid` 自 #217 起被忽略，schema 不能再说「交换联系方式时需要」。

	Agent 以 `boss schema` 为真源构造 argv：照旧描述会先传 `--uid` / `--gid`，
	然后收到「交换联系方式需要 --friend-id」的参数错误，白跑一次。
	"""
	options = SCHEMA_DATA["commands"]["hr"]["options"]["resume"]
	for name in ("--uid", "--gid"):
		description = options[name]["description"]
		assert "已弃用" in description, f"{name} 应标明已弃用: {description}"
		assert "需要" not in description, f"{name} 不应再声称是必需参数: {description}"

def test_schema_option_ranges_match_click_ranges():
	"""schema 的 `minimum` / `maximum` 必须与 Click 的 `IntRange` / `FloatRange` 双向一致。

	`boss schema` 是能力真源：不写下界，Agent 就会尝试 `--page -1`，换回一个
	`INVALID_PARAM`；不写上界（`--port 70000`、`--summary-limit 999`）同样会
	在解析阶段被拒。下界或上界写错（schema 允许 0、Click 只收 1）则让 schema
	本身不可信。缺界、多界、数值不一致都算失败。
	"""
	problems: list[str] = []

	def walk(path: str, node: Any, group: dict[str, Any]) -> None:
		params: dict[str, click.Parameter] = {}
		for param in getattr(node, "params", ()):
			if isinstance(param, click.Argument):
				continue
			for opt in getattr(param, "opts", ()):
				params[opt] = param
				if "/" in opt:
					params["--" + opt.rsplit("/", 1)[-1].lstrip("-")] = param
			for opt in getattr(param, "secondary_opts", ()):
				params[opt] = param

		subgroups: dict[str, dict[str, Any]] = {}
		for key, value in group.items():
			if not isinstance(value, dict):
				continue
			if not str(key).startswith("-"):
				subgroups[str(key)] = value
				continue
			for spelling in _schema_option_spellings(str(key)):
				param = params.get(spelling)
				if param is None:
					continue
				param_type = param.type
				if not isinstance(param_type, (click.IntRange, click.FloatRange)):
					continue
				for bound, click_value in (("minimum", param_type.min), ("maximum", param_type.max)):
					if click_value is None:
						if bound in value:
							problems.append(f"{path} {spelling}: schema 声明了 {bound}，Click 没有")
						continue
					if bound not in value:
						problems.append(f"{path} {spelling}: 缺少 {bound}（Click 取值 {click_value}）")
					elif value[bound] != click_value:
						problems.append(f"{path} {spelling}: {bound}={value[bound]} 与 Click 取值 {click_value} 不一致")

		subcommands = getattr(node, "commands", {}) or {}
		for key, value in subgroups.items():
			if key in subcommands:
				walk(f"{path}.{key}", subcommands[key], value)
		for name, sub in subcommands.items():
			if name not in subgroups:
				walk(f"{path}.{name}", sub, {})

	for name, spec in SCHEMA_DATA["commands"].items():
		node = cli.commands.get(name)
		if node is not None:
			walk(name, node, spec.get("options") or {})

	assert not problems, "schema 取值域与 Click 不一致:\n" + "\n".join(problems)

def test_crawl_run_and_start_share_hook_profile_default():
	"""`crawl run` 与 `crawl start` 的 `--hook-profile` 默认值必须一致且为 none。

	两个命令省略该选项时都按 `hook_profile or "none"` 执行；schema 里 run 写
	"none"、start 写 null，Agent 会以为 start 的「未指定」另有含义，而 null 也
	不在 enum 的可选值里。
	"""
	options = SCHEMA_DATA["commands"]["crawl"]["options"]
	run = options["run"]["--hook-profile"]
	start = options["start"]["--hook-profile"]

	assert run["default"] == start["default"] == "none"
	assert "none" in run["enum"]
	assert "none" in start["enum"]


def test_schema_choice_options_match_click_choices():
	"""Click 用 `Choice` 限制的选项，schema 必须声明同一组取值。

	digest / stats 的 `--format` 在 Click 层只接受固定取值，schema 却没写
	`enum`：Agent 照 schema 可能构造 `--format markdown`，换回一个
	`INVALID_PARAM`。schema 声明的取值可以比 Click 更宽（如 `search --city`
	由 `search_filters` 在运行时校验），这类只做单向检查。
	"""
	problems: list[str] = []

	def walk(path: str, node: Any, group: dict[str, Any]) -> None:
		params: dict[str, click.Parameter] = {}
		for param in getattr(node, "params", ()):
			if isinstance(param, click.Argument):
				continue
			for opt in getattr(param, "opts", ()):
				params[opt] = param
				if "/" in opt:
					params["--" + opt.rsplit("/", 1)[-1].lstrip("-")] = param
			for opt in getattr(param, "secondary_opts", ()):
				params[opt] = param

		subgroups: dict[str, dict[str, Any]] = {}
		for key, value in group.items():
			if not isinstance(value, dict):
				continue
			if not str(key).startswith("-"):
				subgroups[str(key)] = value
				continue
			for spelling in _schema_option_spellings(str(key)):
				param = params.get(spelling)
				if param is None:
					continue
				click_choices = list(param.type.choices) if isinstance(param.type, click.Choice) else None
				schema_choices = value.get("choices") or value.get("enum")
				if click_choices is None:
					continue
				if schema_choices is None:
					problems.append(f"{path} {spelling}: Click 限制为 {click_choices}，schema 未声明")
				elif list(schema_choices) != click_choices:
					problems.append(f"{path} {spelling}: schema {list(schema_choices)} 与 Click {click_choices} 不一致")

		subcommands = getattr(node, "commands", {}) or {}
		for key, value in subgroups.items():
			if key in subcommands:
				walk(f"{path}.{key}", subcommands[key], value)
		for name, sub in subcommands.items():
			if name not in subgroups:
				walk(f"{path}.{name}", sub, {})

	for name, spec in SCHEMA_DATA["commands"].items():
		node = cli.commands.get(name)
		if node is not None:
			walk(name, node, spec.get("options") or {})

	assert not problems, "schema 枚举与 Click Choice 不一致:\n" + "\n".join(problems)


def test_schema_global_delay_default_matches_runtime():
	"""`boss schema` 的 --delay 默认值必须反映实际生效的配置默认值。

	schema 长期写着 1.5-3.0，而 config.DEFAULT_REQUEST_DELAY 早已改为 30-60；
	Agent 会把 1.5-3.0 当成默认节奏，低估真实请求间隔。
	"""
	from boss_agent_cli.config import DEFAULT_REQUEST_DELAY

	low, high = DEFAULT_REQUEST_DELAY
	assert SCHEMA_DATA["global_options"]["--delay"]["default"] == f"{low:g}-{high:g}"
