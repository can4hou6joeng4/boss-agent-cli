"""config 命令测试 — 覆盖查看、设置、重置配置项。"""

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from boss_agent_cli.automation.config import AutomationConfig, automation_config_from_dict
from boss_agent_cli.config import DEFAULTS
from boss_agent_cli.main import cli


_ERROR_KEYS = {"code", "message", "recoverable", "recovery_action"}


def _hint(command: str, data_dir: Path) -> str:
	"""后继提示保留本次调用的数据目录（绝对解析路径）。"""
	return f"boss --data-dir {Path(data_dir).resolve()} {command}"


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


def _invoke_tty(tmp_path, monkeypatch, *args):
	"""按 TTY 口径执行命令，返回 (exit_code, 渲染到终端的文本)。"""
	import io

	from rich.console import Console

	stream = io.StringIO()
	monkeypatch.setattr(
		"boss_agent_cli.display.console",
		Console(file=stream, force_terminal=False, width=120),
	)
	monkeypatch.setattr("boss_agent_cli.display.is_json_mode", lambda ctx: False)
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), *args])
	return result.exit_code, stream.getvalue()


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


def test_config_list_renders_structured_values_in_full(tmp_path, monkeypatch):
	"""结构化配置不能在 25 宽单元格里挤成多行截断文本。

	``automation`` 的字典 repr 有二十多行，两条话术会被截成省略号——用户看不到
	自己配的内容。现在表格里给出占位，完整 JSON 放在表格下方。
	"""
	code, text = _invoke_tty(tmp_path, monkeypatch, "config", "list")
	assert code == 0
	assert "（JSON，见下方）" in text
	assert "您好，想确认下近期是否看机会？" in text
	assert "谢谢回复，我这边同步岗位信息，方便的话可以继续沟通面试时间。" in text
	# 表格里不再出现被 rich 折行的 dict repr。
	assert "'allowed_actions':" not in text


# ── config get ──────────────────────────────────────────────────────


def test_config_get_valid_key(tmp_path):
	"""获取已知配置项应返回值和默认值。"""
	code, parsed = _invoke("config", "get", "log_level", tmp_path=tmp_path)
	assert code == 0
	assert parsed["data"]["key"] == "log_level"
	assert parsed["data"]["value"] == "error"
	assert parsed["data"]["default"] == "error"
	assert parsed["hints"]["next_actions"] == [_hint("config set log_level <值>", tmp_path), _hint("config list", tmp_path)]


def test_config_get_renders_value_in_tty(tmp_path, monkeypatch):
	"""TTY 下 `config get` 必须显示值：之前的 render 是空 lambda，终端一个字都不打。"""
	code, text = _invoke_tty(tmp_path, monkeypatch, "config", "get", "role")
	assert code == 0
	assert "role" in text
	assert "candidate" in text
	# pytest 的临时目录很长，Rich 会在 120 列处折行：分别断言前缀与命令尾部。
	assert "--data-dir" in text
	assert "config set role <值>" in text


def test_config_get_renders_structured_value_as_json(tmp_path, monkeypatch):
	"""结构化值在终端里用格式化 JSON，不再输出会被折行的 Python repr。

	当前值与默认值相同时只展开一次，避免重复四十行相同内容。
	"""
	code, text = _invoke_tty(tmp_path, monkeypatch, "config", "get", "automation")
	assert code == 0
	assert "（JSON，见下方）" in text
	assert "您好，想确认下近期是否看机会？" in text
	assert '"reply_strategy": "hybrid"' in text
	assert "'allowed_actions':" not in text
	assert text.count('"allowed_actions"') == 1


def test_config_get_renders_override_and_default_when_different(tmp_path, monkeypatch):
	"""用户覆盖与默认值不同时，两组 JSON 都要出现，便于对比。"""
	(tmp_path / "config.json").write_text('{"automation": {"mode": "training"}}', encoding="utf-8")
	code, text = _invoke_tty(tmp_path, monkeypatch, "config", "get", "automation")
	assert code == 0
	assert '"mode": "training"' in text
	assert '"default"' in text
	assert '"reply_strategy": "hybrid"' in text


def test_config_get_unknown_key(tmp_path):
	"""获取未知配置项应返回 Agent 可消费的 stdout JSON 错误包络。"""
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "config", "get", "nonexistent"])
	parsed = _assert_single_stdout_json_error(result, command="config", code="INVALID_PARAM")
	assert "可用项" in parsed["error"]["message"]
	# 消息里已经列了可用项，hints 再给出能看到当前值的 config list。
	assert parsed["hints"]["next_actions"] == [_hint("config list", tmp_path)]


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
	assert parsed["hints"]["next_actions"] == [_hint("config get log_level", tmp_path), _hint("config list", tmp_path)]


def test_config_set_renders_confirmation_in_tty(tmp_path, monkeypatch):
	"""TTY 下 `config set` 静默成功时，真人只能怀疑自己没按回车。"""
	code, text = _invoke_tty(tmp_path, monkeypatch, "config", "set", "log_level", "debug")
	assert code == 0
	assert "log_level" in text
	assert "debug" in text
	saved = json.loads((tmp_path / "config.json").read_text())
	assert saved["log_level"] == "debug"


def test_config_set_renders_structured_values_as_json(tmp_path, monkeypatch):
	"""设置结构化配置时，新值与上一次的值都以格式化 JSON 呈现。"""
	code, text = _invoke_tty(tmp_path, monkeypatch, "config", "set", "automation", '{"mode": "training"}')
	assert code == 0
	assert "（JSON，见下方）" in text
	assert '"mode": "training"' in text
	assert '"previous"' in text
	assert '"reply_strategy": "hybrid"' in text


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


@pytest.mark.parametrize("value", [":::", "http://", "localhost:9222", "http://127.0.0.1:99999", "ftp://127.0.0.1:1"])
def test_config_set_rejects_invalid_cdp_url(tmp_path, value):
	"""坏 cdp_url 会让之后每条命令在启动期失败，写入时就必须拒绝。

	`--cdp-url` 已在启动期按同一规则校验；`config set cdp_url` 若照常落盘，
	用户会得到 ok=true，然后所有命令都换回 INVALID_PARAM。
	"""
	code, parsed = _invoke("config", "set", "cdp_url", value, tmp_path=tmp_path)

	assert code == 1
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert "http(s) 调试地址" in parsed["error"]["message"]
	assert parsed["hints"]["next_actions"] == [_hint("config get cdp_url", tmp_path), _hint("config list", tmp_path)]
	written = tmp_path / "config.json"
	assert not written.exists() or "cdp_url" not in json.loads(written.read_text(encoding="utf-8"))


def test_config_set_accepts_valid_cdp_url(tmp_path):
	"""合法 CDP 地址写入后，普通命令能通过启动期校验。"""
	code, parsed = _invoke("config", "set", "cdp_url", "http://127.0.0.1:9222", tmp_path=tmp_path)
	assert code == 0
	assert parsed["data"]["value"] == "http://127.0.0.1:9222"

	code, parsed = _invoke("config", "list", tmp_path=tmp_path)
	assert code == 0
	item = next(i for i in parsed["data"]["items"] if i["key"] == "cdp_url")
	assert item["value"] == "http://127.0.0.1:9222"


def test_config_set_role_validates_choice(tmp_path):
	"""role 进 _CONFIG_CHOICES：写进非法值时命令本身就该失败。

	以前 `config set role admin` 返回 ok=true，落盘后每条命令都在启动阶段
	返回 INVALID_PARAM，连 `config reset role` 也一样（只能靠 `--role` 覆盖）。
	"""
	code, parsed = _invoke("config", "set", "role", "recruiter", tmp_path=tmp_path)
	assert code == 0
	assert parsed["data"]["value"] == "recruiter"
	assert json.loads((tmp_path / "config.json").read_text())["role"] == "recruiter"

	code, parsed = _invoke("config", "set", "role", "admin", tmp_path=tmp_path)
	assert code == 1
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert "candidate, recruiter" in parsed["error"]["message"]
	# 非法值没有落盘：之后的命令（含 config reset）仍然可用。
	assert json.loads((tmp_path / "config.json").read_text())["role"] == "recruiter"
	code, parsed = _invoke("config", "get", "role", tmp_path=tmp_path)
	assert code == 0
	assert parsed["data"]["value"] == "recruiter"


def test_config_set_log_level_validates_choice(tmp_path):
	"""log_level 进 _CONFIG_CHOICES：Logger 对未知级别静默回退到 error 阈值，
	所以写入期必须拒绝拼错的级别，而不是报成功却什么都不记。"""
	code, parsed = _invoke("config", "set", "log_level", "debug", tmp_path=tmp_path)
	assert code == 0
	assert parsed["data"]["value"] == "debug"

	code, parsed = _invoke("config", "set", "log_level", "verbose", tmp_path=tmp_path)
	assert code == 1
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert "error, warning, info, debug" in parsed["error"]["message"]
	assert json.loads((tmp_path / "config.json").read_text())["log_level"] == "debug"


def test_config_choices_cover_enum_valued_defaults():
	"""有固定取值域的配置项都要进 _CONFIG_CHOICES，否则 config set 能把非法值写进配置。"""
	from boss_agent_cli.commands.config_cmd import _CONFIG_CHOICES

	assert {"operating_mode", "browser_source", "role", "log_level"} <= set(_CONFIG_CHOICES)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 的 Path.expanduser 不解析 ~user")
def test_unknown_home_in_request_budget_dir_returns_envelope(tmp_path):
	"""config.json 里 `~未知用户` 的预算目录不能让整条命令冲出 traceback。"""
	(tmp_path / "config.json").write_text(json.dumps({"request_budget_dir": "~boss_cli_no_such_user_zzz/x"}), encoding="utf-8")

	code, parsed = _invoke("status", tmp_path=tmp_path)

	assert code == 1
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert "家目录写法无法展开" in parsed["error"]["message"]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 的 Path.expanduser 不解析 ~user")
def test_config_set_unknown_home_budget_dir_is_rejected(tmp_path):
	"""`config set request_budget_dir "~未知用户/x"` 要按参数错误拒绝且不写盘。"""
	code, parsed = _invoke("config", "set", "request_budget_dir", "~boss_cli_no_such_user_zzz/x", tmp_path=tmp_path)

	assert code == 1
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert "家目录写法无法展开" in parsed["error"]["message"]
	written = tmp_path / "config.json"
	assert not written.exists() or "request_budget_dir" not in json.loads(written.read_text(encoding="utf-8"))


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 的 Path.expanduser 不解析 ~user")
def test_budget_directory_unknown_home_is_value_error(tmp_path):
	"""运行期解析预算目录同样把 ~未知用户 归一成 ValueError，而不是 RuntimeError。"""
	from boss_agent_cli.api.request_budget import budget_directory

	(tmp_path / "config.json").write_text(json.dumps({"request_budget_dir": "~boss_cli_no_such_user_zzz/x"}), encoding="utf-8")

	with pytest.raises(ValueError, match="家目录写法无法展开"):
		budget_directory(tmp_path)


@pytest.mark.parametrize("value", [5, True, ["auto"], {"name": "auto"}])
def test_non_string_browser_source_in_config_returns_envelope(tmp_path, value):
	"""config.json 里 browser_source 不是字符串时不能 traceback，要按参数错误给信封。"""
	(tmp_path / "config.json").write_text(json.dumps({"browser_source": value}), encoding="utf-8")

	code, parsed = _invoke("config", "list", tmp_path=tmp_path)

	assert code == 1
	assert parsed["error"]["code"] == "INVALID_PARAM"
	message = parsed["error"]["message"]
	assert "不支持的浏览器通道" in message
	assert repr(value) in message
	assert "auto" in message


def test_browser_source_override_recovers_from_non_string_config(tmp_path):
	"""启动期校验挡住坏配置时，--browser-source 覆盖仍要能执行修复命令。"""
	(tmp_path / "config.json").write_text(json.dumps({"browser_source": 5}), encoding="utf-8")

	runner = CliRunner()
	result = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--browser-source", "auto", "config", "reset", "browser_source"],
	)

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8")) == {}


@pytest.mark.parametrize("value", [9, True, ["http://127.0.0.1:9222"]])
def test_non_string_cdp_url_in_config_returns_envelope(tmp_path, value):
	"""config.json 的 cdp_url 不是字符串时，doctor 等命令不能抛 shlex TypeError。"""
	(tmp_path / "config.json").write_text(json.dumps({"cdp_url": value}), encoding="utf-8")

	code, parsed = _invoke("doctor", tmp_path=tmp_path)

	assert code == 1
	assert parsed["error"]["code"] == "INVALID_PARAM"
	message = parsed["error"]["message"]
	assert "--cdp-url" in message
	assert "调试地址字符串或 null" in message
	assert repr(value) in message


def test_cdp_url_override_recovers_from_non_string_config(tmp_path):
	"""坏 cdp_url 挡住启动时，--cdp-url 覆盖仍要能执行修复命令。"""
	(tmp_path / "config.json").write_text(json.dumps({"cdp_url": 9}), encoding="utf-8")

	runner = CliRunner()
	result = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--cdp-url", "http://127.0.0.1:9222", "config", "reset", "cdp_url"],
	)

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8")) == {}


@pytest.mark.parametrize("value", [5, "admin", ["recruiter"]])
def test_invalid_role_in_config_returns_envelope(tmp_path, value):
	"""config.json 的 role 写成数字或无效值时，doctor 等命令不能抛 shlex TypeError。"""
	(tmp_path / "config.json").write_text(json.dumps({"role": value}), encoding="utf-8")

	code, parsed = _invoke("doctor", tmp_path=tmp_path)

	assert code == 1
	assert parsed["error"]["code"] == "INVALID_PARAM"
	message = parsed["error"]["message"]
	assert "--role" in message
	assert repr(value) in message
	assert "candidate" in message
	assert "recruiter" in message


def test_valid_role_in_config_is_accepted(tmp_path):
	"""config.json 里的角色值要与 --role 的取值域一致，合法值不应被启动期校验拦住。"""
	(tmp_path / "config.json").write_text(json.dumps({"role": "recruiter"}), encoding="utf-8")

	code, parsed = _invoke("config", "get", "role", tmp_path=tmp_path)

	assert code == 0
	assert parsed["data"]["value"] == "recruiter"


def test_role_override_recovers_from_invalid_config(tmp_path):
	"""坏 role 挡住启动时，--role 覆盖仍要能执行修复命令。"""
	(tmp_path / "config.json").write_text(json.dumps({"role": 5}), encoding="utf-8")

	runner = CliRunner()
	result = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--role", "candidate", "config", "reset", "role"],
	)

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8")) == {}


def test_parse_value_int_from_default_type():
	"""_parse_value 按 int 默认值类型转换输入。"""
	from boss_agent_cli.commands.config_cmd import _parse_value
	result = _parse_value("request_max_per_minute", "5", 10)
	assert result == 5
	assert isinstance(result, int)


@pytest.mark.parametrize(
	("key", "raw", "default", "fragment"),
	[
		("request_max_per_minute", "abc", 10, "必须是整数"),
		("request_wait_timeout", "abc", 30.0, "必须是数值"),
		("request_delay", "1.5;3", [1.5, 3.0], "必须是逗号分隔的数值列表"),
	],
)
def test_parse_value_reports_chinese_message_with_key(key, raw, default, fragment):
	"""转换失败要点名配置项并给中文说明，而不是裸 int()/float() 的英文报错。"""
	from boss_agent_cli.commands.config_cmd import _parse_value

	with pytest.raises(ValueError) as excinfo:
		_parse_value(key, raw, default)

	message = str(excinfo.value)
	assert key in message
	assert raw in message
	assert fragment in message


@pytest.mark.parametrize(
	("key", "value"),
	[
		("request_max_per_minute", "abc"),
		("request_wait_timeout", "abc"),
		("request_delay", "abc"),
	],
)
def test_config_set_numeric_parse_error_returns_chinese_envelope(tmp_path, key, value):
	"""数值配置项写错时信封里的 message 是中文，且点名配置项。"""
	code, parsed = _invoke("config", "set", key, value, tmp_path=tmp_path)

	assert code == 1
	assert parsed["error"]["code"] == "INVALID_PARAM"
	message = parsed["error"]["message"]
	assert key in message
	assert value in message
	assert "could not convert" not in message
	assert "invalid literal" not in message
	assert parsed["hints"]["next_actions"] == [_hint(f"config get {key}", tmp_path), _hint("config list", tmp_path)]
	assert _hint("config list", tmp_path) in parsed["error"]["recovery_action"]


def test_config_set_invalid_choice_points_at_config_commands(tmp_path):
	"""枚举值写错时同样要给出查看当前配置的下一步，而不只是「修正参数」。"""
	code, parsed = _invoke("config", "set", "log_level", "bogus", tmp_path=tmp_path)

	assert code == 1
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert parsed["error"]["message"] == "log_level 必须是以下值之一: error, warning, info, debug"
	assert parsed["hints"]["next_actions"] == [_hint("config get log_level", tmp_path), _hint("config list", tmp_path)]
	assert _hint("config list", tmp_path) in parsed["error"]["recovery_action"]


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
		'{"auto_execute_threshold": -1}',
		'{"max_per_tab": -1}',
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


def test_defaults_automation_matches_runtime_parser():
	"""``config list`` 展示的 automation 默认值必须与解析器完全一致。

	手写副本会漂移：``platforms`` 字段删除后它仍留在 DEFAULTS 里被展示（配了不生效），
	而 ``tabs`` / ``reply_strategy`` 等七个字段反而看不到。新增或删除字段却忘了同步
	这份默认值时，这条断言会失败。
	"""
	listed = DEFAULTS["automation"]
	assert set(listed) == set(AutomationConfig.__dataclass_fields__)
	# 同一份数据交给正式解析器必须得到全新构造的默认配置，不再有任何兜底差异。
	assert automation_config_from_dict(listed) == AutomationConfig()


def test_defaults_crawl_lists_only_consumed_keys():
	"""crawl 默认值只保留 run/start/configure 真正读取的键。

	``hook_profile`` / ``hook_dir`` 曾出现在默认值里，但采集只能通过
	``boss crawl run --hook-profile ... --hook-dir ...`` 显式指定，config.json 里的
	同名键从未被读取。多一个「能配不生效」的键比少一个更糟。
	"""
	assert set(DEFAULTS["crawl"]) == {
		"chrome_path",
		"cdp_port",
		"max_requests",
		"max_details",
		"max_seconds",
		"max_retries",
	}


@pytest.mark.parametrize(
	"value",
	[
		'{"max_requests": 0}',
		'{"max_requests": [1]}',
		'{"max_retries": -1}',
		'{"cdp_port": 70000}',
		'{"cdp_port": "abc"}',
		'{"chrome_path": 5}',
	],
)
def test_config_set_crawl_rejects_invalid_values(tmp_path, value):
	"""采集配置写错时不能先落盘，否则每次 crawl run/start 都要报一次 INVALID_PARAM。"""
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "config", "set", "crawl", value])
	parsed = _assert_single_stdout_json_error(result, command="config", code="INVALID_PARAM")
	assert "crawl 配置无效" in parsed["error"]["message"]
	assert not (tmp_path / "config.json").exists()


def test_config_set_crawl_accepts_partial_object(tmp_path):
	"""显式 0 重试与部分对象都必须保留，不能被默认值覆盖。"""
	code, parsed = _invoke("config", "set", "crawl", '{"max_retries": 0}', tmp_path=tmp_path)
	assert code == 0
	assert parsed["data"]["value"] == {"max_retries": 0}
	saved = json.loads((tmp_path / "config.json").read_text())
	assert saved["crawl"] == {"max_retries": 0}


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
	assert parsed["hints"]["next_actions"] == [_hint("config list", tmp_path)]


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
	assert parsed["hints"]["next_actions"] == [_hint("config get log_level", tmp_path), _hint("config list", tmp_path)]
	# 验证文件中已移除
	config_file = tmp_path / "config.json"
	saved = json.loads(config_file.read_text())
	assert "log_level" not in saved


def test_config_reset_renders_confirmation_in_tty(tmp_path, monkeypatch):
	"""TTY 下 `config reset` 同样要确认恢复成了哪个值。"""
	_invoke("config", "set", "log_level", "debug", tmp_path=tmp_path)
	code, text = _invoke_tty(tmp_path, monkeypatch, "config", "reset", "log_level")
	assert code == 0
	assert "log_level" in text
	assert "restored" in text


def test_config_reset_renders_structured_default_as_json(tmp_path, monkeypatch):
	"""恢复结构化配置时，默认值以完整 JSON 呈现而不是被折行的 repr。"""
	_invoke("config", "set", "automation", '{"mode": "training"}', tmp_path=tmp_path)
	code, text = _invoke_tty(tmp_path, monkeypatch, "config", "reset", "automation")
	assert code == 0
	assert "（JSON，见下方）" in text
	assert '"mode": "autonomous"' in text
	assert "'allowed_actions':" not in text


def test_config_reset_unknown_key(tmp_path):
	"""重置未知配置项应返回 Agent 可消费的 stdout JSON 错误包络。"""
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "config", "reset", "bad_key"])
	parsed = _assert_single_stdout_json_error(result, command="config", code="INVALID_PARAM")
	assert "可用项" in parsed["error"]["message"]
	assert parsed["hints"]["next_actions"] == [_hint("config list", tmp_path)]


def test_unreadable_config_file_returns_envelope_instead_of_traceback(tmp_path):
	"""config.json 读不出来时，连 config list 都要给信封和修复路径。"""
	(tmp_path / "config.json").mkdir()
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "config", "list"])
	parsed = _assert_single_stdout_json_error(result, command="boss", code="INVALID_PARAM")
	assert "无法读取" in parsed["error"]["message"]
	assert "config.json" in parsed["error"]["message"]


def test_corrupt_config_json_names_the_file_and_position(tmp_path):
	"""语法坏掉的 config.json 会挡住所有命令，消息必须给出要修的文件与行列。"""
	config_file = tmp_path / "config.json"
	config_file.write_text('{"log_level":', encoding="utf-8")
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "config", "reset", "log_level"])
	parsed = _assert_single_stdout_json_error(result, command="boss", code="INVALID_PARAM")
	message = parsed["error"]["message"]
	assert str(config_file) in message
	assert "不是合法 JSON" in message
	assert "第 1 行" in message
	assert "删除" in message


@pytest.mark.parametrize("content", ["[]", '"debug"', "null", "42"])
def test_non_object_config_json_names_the_file(tmp_path, content):
	config_path = tmp_path / "config.json"
	config_path.write_text(content, encoding="utf-8")
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "config", "list"])
	parsed = _assert_single_stdout_json_error(result, command="boss", code="INVALID_PARAM")
	assert "必须是 JSON 对象" in parsed["error"]["message"]
	assert str(config_path) in parsed["error"]["message"]


_READ_ONLY_UNSUPPORTED = os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0)


@pytest.mark.skipif(_READ_ONLY_UNSUPPORTED, reason="只读目录在 Windows 与 root 下不生效")
@pytest.mark.parametrize(
	"args",
	[
		("set", "log_level", "debug"),
		("reset", "log_level"),
	],
)
def test_config_write_failure_returns_local_io_error(tmp_path, args):
	"""配置文件写不进去时报本地 IO 错误（不可自动重试），而不是 traceback。"""
	data_dir = tmp_path / "readonly"
	data_dir.mkdir()
	data_dir.chmod(0o500)
	try:
		result = CliRunner().invoke(cli, ["--data-dir", str(data_dir), "config", *args])
	finally:
		data_dir.chmod(0o700)
	parsed = _assert_single_stdout_json_error(result, command="config", code="LOCAL_IO_ERROR")
	assert "写入" in parsed["error"]["message"]
	assert parsed["error"]["recoverable"] is False
	assert "不要自动重试" in parsed["error"]["recovery_action"]


def test_config_write_failure_keeps_previous_file(tmp_path, monkeypatch):
	"""原子写失败时保留原配置：半截 config.json 会让所有命令都卡在启动阶段。"""
	config_file = tmp_path / "config.json"
	config_file.write_text('{"log_level": "debug"}\n', encoding="utf-8")
	original = config_file.read_bytes()

	def fail_replace(source, destination):
		raise OSError("disk full")

	monkeypatch.setattr(os, "replace", fail_replace)
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "config", "set", "log_level", "info"])

	_assert_single_stdout_json_error(result, command="config", code="LOCAL_IO_ERROR")
	assert config_file.read_bytes() == original


# ── JSON 信封格式 ──────────────────────────────────────────────────


def test_config_json_envelope_structure(tmp_path):
	"""验证 config 输出符合 JSON 信封规范。"""
	code, parsed = _invoke("config", "list", tmp_path=tmp_path)
	assert "ok" in parsed
	assert "schema_version" in parsed
	assert "command" in parsed
	assert "data" in parsed
	assert parsed["command"] == "config"


@pytest.mark.parametrize("raw", ['"debug "', "3", "[]", "null", '"verbose"'])
def test_startup_rejects_invalid_config_log_level(tmp_path, raw):
	"""config.json 里的 log_level 坏值不能让每条命令静默按 error 阈值跑。

	``Logger`` 对未知级别用 ``_LEVEL_ORDER.get(level, 3)``：用户以为打开了
	debug 却什么都看不到；非字符串值还会在字典查找处抛 TypeError。
	"""
	(tmp_path / "config.json").write_text(f'{{"log_level": {raw}}}', encoding="utf-8")
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "status"])
	parsed = _assert_single_stdout_json_error(result, command="boss", code="INVALID_PARAM")
	assert "log_level" in parsed["error"]["message"]


@pytest.mark.parametrize("raw", ['"30-60"', "30", "[]", "null"])
def test_startup_rejects_invalid_request_delay_shape(tmp_path, raw):
	"""坏形状不能被静默换成默认区间：用户以为节流生效，实际跑的是 30-60。"""
	(tmp_path / "config.json").write_text(f'{{"request_delay": {raw}}}', encoding="utf-8")
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "status"])
	parsed = _assert_single_stdout_json_error(result, command="boss", code="INVALID_PARAM")
	assert "request_delay" in parsed["error"]["message"]


@pytest.mark.parametrize("key, raw", [("log_level", '"verbose"'), ("request_delay", '"30-60"')])
def test_config_reset_repairs_invalid_startup_config(tmp_path, key, raw):
	"""坏值必须放行 config 子命令，否则用户没有命令行手段把它改回来。"""
	(tmp_path / "config.json").write_text(f'{{"{key}": {raw}}}', encoding="utf-8")
	code, parsed = _invoke("config", "reset", key, tmp_path=tmp_path)
	assert code == 0
	assert parsed["ok"] is True
