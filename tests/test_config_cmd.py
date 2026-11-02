"""config 命令测试 — 覆盖查看、设置、重置配置项。"""

import json
from typing import Any

import pytest
from click.testing import CliRunner, Result

from boss_agent_cli.main import cli


_ERROR_KEYS = {"code", "message", "recoverable", "recovery_action"}


def _assert_single_stdout_json_error(result: Result, *, command: str, code: str) -> dict[str, Any]:
	"""Agent-facing error output must stay machine-consumable on stdout only."""
	assert result.exit_code == 1
	assert result.stderr == ""
	assert result.output.strip()
	assert "\n" not in result.output.strip()
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["schema_version"] == "1.0"
	assert parsed["command"] == command
	assert parsed["data"] is None
	assert parsed["pagination"] is None
	assert set(parsed["error"]) == _ERROR_KEYS
	assert parsed["error"]["code"] == code
	assert isinstance(parsed["error"]["message"], str)
	assert isinstance(parsed["error"]["recoverable"], bool)
	return parsed


def _invoke(*args, tmp_path=None):
	runner = CliRunner()
	cli_args = []
	if tmp_path is not None:
		cli_args.extend(["--data-dir", str(tmp_path)])
	cli_args.extend(args)
	result = runner.invoke(cli, cli_args)
	return result.exit_code, json.loads(result.output) if result.output.strip() else None


# ── config list ─────────────────────────────────────────────────────


def test_config_list_returns_all_defaults(tmp_path):
	"""无自定义配置时应返回全部默认项。"""
	code, parsed = _invoke("config", "list", tmp_path=tmp_path)
	assert code == 0
	assert parsed["ok"] is True
	items = parsed["data"]["items"]
	keys = {item["key"] for item in items}
	assert "log_level" in keys
	assert "cdp_url" in keys
	assert "request_delay" in keys
	assert "operating_mode" in keys
	assert "low_risk_mode" not in keys
	for item in items:
		assert item["source"] == "默认值"


def test_config_list_shows_user_overrides(tmp_path):
	"""有自定义配置时应标记为用户配置。"""
	config_file = tmp_path / "config.json"
	config_file.write_text('{"log_level": "debug"}', encoding="utf-8")
	code, parsed = _invoke("config", "list", tmp_path=tmp_path)
	assert code == 0
	log_item = next(i for i in parsed["data"]["items"] if i["key"] == "log_level")
	assert log_item["value"] == "debug"
	assert log_item["source"] == "用户配置"


def test_config_without_subcommand_shows_list(tmp_path):
	"""不带子命令时应等同于 config list。"""
	code, parsed = _invoke("config", tmp_path=tmp_path)
	assert code == 0
	assert "items" in parsed["data"]


# ── config get ──────────────────────────────────────────────────────


def test_config_get_valid_key(tmp_path):
	"""获取已知配置项应返回值和默认值。"""
	code, parsed = _invoke("config", "get", "log_level", tmp_path=tmp_path)
	assert code == 0
	assert parsed["data"]["key"] == "log_level"
	assert parsed["data"]["value"] == "error"
	assert parsed["data"]["default"] == "error"


def test_config_get_unknown_key(tmp_path):
	"""获取未知配置项应返回 Agent 可消费的 stdout JSON 错误包络。"""
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "config", "get", "nonexistent"])
	parsed = _assert_single_stdout_json_error(result, command="config", code="INVALID_PARAM")
	assert "可用项" in parsed["error"]["message"]
	assert parsed["hints"] is None


# ── config set ──────────────────────────────────────────────────────


def test_config_set_string_value(tmp_path):
	"""设置字符串类型配置项。"""
	code, parsed = _invoke("config", "set", "log_level", "debug", tmp_path=tmp_path)
	assert code == 0
	assert parsed["data"]["key"] == "log_level"
	assert parsed["data"]["value"] == "debug"
	# 验证持久化
	config_file = tmp_path / "config.json"
	saved = json.loads(config_file.read_text())
	assert saved["log_level"] == "debug"


def test_config_set_operating_mode_validates_choice(tmp_path):
	code, parsed = _invoke("config", "set", "operating_mode", "research", tmp_path=tmp_path)
	assert code == 0
	assert parsed["data"]["value"] == "research"

	code, parsed = _invoke("config", "set", "operating_mode", "invalid", tmp_path=tmp_path)
	assert code == 1
	assert parsed["error"]["code"] == "INVALID_PARAM"


def test_config_set_platform_uses_current_registry(tmp_path, monkeypatch):
	"""写入时读取注册表，允许合法平台及调用前新增的适配器。"""
	from boss_agent_cli import platforms

	for name in ("zhipin", "candidate-only"):
		if name == "candidate-only":
			monkeypatch.setitem(platforms._REGISTRY, name, platforms.BossPlatform)
		code, parsed = _invoke("config", "set", "platform", name, tmp_path=tmp_path)
		assert code == 0
		assert parsed["data"]["value"] == name
		assert json.loads((tmp_path / "config.json").read_text())["platform"] == name
		code, parsed = _invoke("config", "get", "platform", tmp_path=tmp_path)
		assert code == 0
		assert parsed["data"]["value"] == name


@pytest.mark.parametrize("value", ["zhilian", "qiancheng", "51job", "unknown", "", " ZHIPIN "])
@pytest.mark.parametrize("existing", [False, True])
def test_config_set_invalid_platform_does_not_write(tmp_path, value, existing):
	config_file = tmp_path / "config.json"
	original = b'{"log_level": "debug", "request_delay": [2, 4]}\n'
	if existing:
		config_file.write_bytes(original)

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "config", "set", "platform", value])
	parsed = _assert_single_stdout_json_error(result, command="config", code="INVALID_PARAM")
	assert "zhipin" in parsed["error"]["message"]
	if existing:
		assert config_file.read_bytes() == original
	else:
		assert not config_file.exists()


def test_config_set_browser_source_validates_choice(tmp_path):
	"""browser_source 进 _CONFIG_CHOICES：合法值可设，非法值拒绝（且 reset 不被拦）。"""
	code, parsed = _invoke("config", "set", "browser_source", "stored-cookie", tmp_path=tmp_path)
	assert code == 0
	assert parsed["data"]["value"] == "stored-cookie"

	code, parsed = _invoke("config", "set", "browser_source", "cdp", tmp_path=tmp_path)
	assert code == 1
	assert parsed["error"]["code"] == "INVALID_PARAM"

	# 合法值写入后，reset 必须能正常恢复默认（不被 group callback 拦成 INVALID_PARAM）。
	code, parsed = _invoke("config", "reset", "browser_source", tmp_path=tmp_path)
	assert code == 0
	assert parsed["data"]["value"] == "auto"


def test_parse_value_int_from_default_type():
	"""_parse_value 按 int 默认值类型转换输入。"""
	from boss_agent_cli.commands.config_cmd import _parse_value
	result = _parse_value("5", 10)
	assert result == 5
	assert isinstance(result, int)


def test_config_set_list_value(tmp_path):
	"""设置列表类型配置项。"""
	code, parsed = _invoke("config", "set", "request_delay", "2.0,4.0", tmp_path=tmp_path)
	assert code == 0
	assert parsed["data"]["value"] == [2.0, 4.0]


def test_config_set_null_value(tmp_path):
	"""设置可空配置项为 null。"""
	# 先设一个值
	_invoke("config", "set", "cdp_url", "http://localhost:9222", tmp_path=tmp_path)
	# 再重置为 null
	code, parsed = _invoke("config", "set", "cdp_url", "null", tmp_path=tmp_path)
	assert code == 0
	assert parsed["data"]["value"] is None


@pytest.mark.parametrize("key", ["automation", "crawl"])
@pytest.mark.parametrize("value", ["foo", "[]", "42", "null"])
def test_config_set_structured_value_requires_json_object(tmp_path, key, value):
	"""structured key 不接受标量文本：写进去会让后续读取方在 .get() 上崩溃。"""
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "config", "set", key, value])
	parsed = _assert_single_stdout_json_error(result, command="config", code="INVALID_PARAM")
	assert "JSON object" in parsed["error"]["message"]
	assert not (tmp_path / "config.json").exists()


def test_config_set_structured_value_accepts_json_object(tmp_path):
	code, parsed = _invoke("config", "set", "automation", '{"mode": "training"}', tmp_path=tmp_path)
	assert code == 0
	assert parsed["data"]["value"] == {"mode": "training"}
	saved = json.loads((tmp_path / "config.json").read_text())
	assert saved["automation"] == {"mode": "training"}


@pytest.mark.parametrize(
	"value",
	[
		'{"mode": "nonsense"}',
		'{"reply_strategy": "nonsense"}',
		'{"human_review_threshold": "high"}',
		'{"allowed_actions": 5}',
	],
)
def test_config_set_automation_rejects_invalid_values(tmp_path, value):
	"""枚举 / 数值字段写错时不能先落盘，否则 agent run/train 运行时才炸。"""
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "config", "set", "automation", value])
	parsed = _assert_single_stdout_json_error(result, command="config", code="INVALID_PARAM")
	assert "automation 配置无效" in parsed["error"]["message"]
	assert not (tmp_path / "config.json").exists()


def test_config_set_automation_accepts_partial_object(tmp_path):
	"""缺省字段由解析器补默认值，内容校验不能误伤部分配置。"""
	code, parsed = _invoke("config", "set", "automation", '{"max_per_tab": 5}', tmp_path=tmp_path)
	assert code == 0
	assert parsed["data"]["value"] == {"max_per_tab": 5}


def test_agent_run_rejects_non_object_automation_config(tmp_path):
	"""手工编辑 config.json 写成字符串时，agent 也要给信封与修复入口，而不是 traceback。"""
	(tmp_path / "config.json").write_text('{"automation": "foo"}', encoding="utf-8")
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "agent", "run", "--dry-run"])
	parsed = _assert_single_stdout_json_error(result, command="agent.run", code="INVALID_PARAM")
	assert "JSON object" in parsed["error"]["message"]
	assert parsed["error"]["recoverable"] is True
	assert "config reset automation" in (parsed["error"]["recovery_action"] or "")


@pytest.mark.parametrize(
	("args", "command"),
	[
		(("agent", "run", "--dry-run"), "agent.run"),
		(("agent", "train"), "agent.train"),
	],
)
def test_agent_rejects_invalid_automation_value(tmp_path, args, command):
	"""手工写入非法枚举值时，agent run/train 同样要给信封而不是 traceback。"""
	(tmp_path / "config.json").write_text('{"automation": {"mode": "nonsense"}}', encoding="utf-8")
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), *args])
	parsed = _assert_single_stdout_json_error(result, command=command, code="INVALID_PARAM")
	assert "automation 配置无效" in parsed["error"]["message"]
	assert parsed["error"]["recoverable"] is True
	assert "config reset automation" in (parsed["error"]["recovery_action"] or "")


def test_config_set_unknown_key(tmp_path):
	"""设置未知配置项应返回 Agent 可消费的 stdout JSON 错误包络。"""
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "config", "set", "bad_key", "val"])
	parsed = _assert_single_stdout_json_error(result, command="config", code="INVALID_PARAM")
	assert "可用项" in parsed["error"]["message"]


def test_config_commands_do_not_expose_internal_low_risk_policy(tmp_path):
	"""低风险策略不是普通用户配置项，不通过 config 命令提供恢复路径。"""
	for args in (
		("get", "low_risk_mode"),
		("set", "low_risk_mode", "false"),
		("reset", "low_risk_mode"),
	):
		code, parsed = _invoke("config", *args, tmp_path=tmp_path)
		assert code == 1
		assert parsed["error"]["code"] == "INVALID_PARAM"
		assert "low_risk_mode" not in parsed["error"]["message"]


# ── config reset ────────────────────────────────────────────────────


def test_config_reset_restores_default(tmp_path):
	"""重置配置项应恢复默认值并从文件中移除。"""
	# 先设置
	_invoke("config", "set", "log_level", "debug", tmp_path=tmp_path)
	# 再重置
	code, parsed = _invoke("config", "reset", "log_level", tmp_path=tmp_path)
	assert code == 0
	assert parsed["data"]["value"] == "error"
	assert parsed["data"]["restored"] is True
	# 验证文件中已移除
	config_file = tmp_path / "config.json"
	saved = json.loads(config_file.read_text())
	assert "log_level" not in saved


def test_config_reset_unknown_key(tmp_path):
	"""重置未知配置项应返回 Agent 可消费的 stdout JSON 错误包络。"""
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "config", "reset", "bad_key"])
	parsed = _assert_single_stdout_json_error(result, command="config", code="INVALID_PARAM")
	assert "可用项" in parsed["error"]["message"]


# ── JSON 信封格式 ──────────────────────────────────────────────────


def test_config_json_envelope_structure(tmp_path):
	"""验证 config 输出符合 JSON 信封规范。"""
	code, parsed = _invoke("config", "list", tmp_path=tmp_path)
	assert "ok" in parsed
	assert "schema_version" in parsed
	assert "command" in parsed
	assert "data" in parsed
	assert parsed["command"] == "config"
