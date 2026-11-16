"""config.json 坏值不得挡住 `boss config reset`（Issue #1168）。"""

import json

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli


def _invoke(tmp_path, *args, json_mode=True):
	cli_args = ["--data-dir", str(tmp_path)]
	if json_mode:
		cli_args.append("--json")
	cli_args.extend(args)
	result = CliRunner().invoke(cli, cli_args)
	parsed = json.loads(result.output) if result.output.strip().startswith("{") else None
	return result.exit_code, parsed


_BAD_VALUES = [
	("role", '"5"'),
	("role", '"每"'),
	("platform", '"linkedin"'),
	("platform", "5"),
	("browser_source", "5"),
	("browser_source", '["auto"]'),
	("cdp_url", "5"),
	("cdp_url", "[]"),
]


@pytest.mark.parametrize(("key", "raw"), _BAD_VALUES)
def test_config_reset_repairs_bad_enum_value(tmp_path, key, raw):
	"""坏配置必须在 config 子命令里可修复，而不是把 reset 也挡在启动期。"""
	(tmp_path / "config.json").write_text("{" + f'"{key}": {raw}' + "}", encoding="utf-8")

	code, parsed = _invoke(tmp_path, "config", "reset", key)

	assert code == 0, parsed
	assert parsed["ok"] is True
	assert parsed["data"]["restored"] is True
	assert key not in json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(("key", "raw"), _BAD_VALUES)
def test_other_commands_still_reject_bad_enum_value(tmp_path, key, raw):
	"""放行只限 config 子命令：普通命令仍按参数错误拒绝，不能静默按默认值跑。"""
	(tmp_path / "config.json").write_text("{" + f'"{key}": {raw}' + "}", encoding="utf-8")

	code, parsed = _invoke(tmp_path, "doctor")

	assert code == 1
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert key.replace("_", "-") in parsed["error"]["message"] or key in parsed["error"]["message"]


def test_config_reset_works_after_bad_platform(tmp_path):
	"""坏 platform 会挡住 list_platforms 之外的一切；reset 必须能落地。"""
	(tmp_path / "config.json").write_text(json.dumps({"platform": "linkedin", "role": "5"}), encoding="utf-8")

	code, parsed = _invoke(tmp_path, "config", "reset", "platform")
	assert code == 0, parsed
	code, parsed = _invoke(tmp_path, "config", "reset", "role")
	assert code == 0, parsed
	assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8")) == {}

	code, parsed = _invoke(tmp_path, "config", "get", "platform")
	assert code == 0
	assert parsed["data"]["value"] == "zhipin"
