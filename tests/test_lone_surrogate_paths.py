"""孤立代理字符的本地写入与信封序列化边界（Issue #1294）。

argv 里的非法 UTF-8 字节会被 Python 解码成 U+DCFF 之类的孤立代理；手改 /
损坏的配置与平台字段也可能含 ``\\ud800``。这些字符无法按 UTF-8 编码：写入
本地 JSON、序列化输出信封时都会抛 ``UnicodeEncodeError``，命令丢掉整个信封，
MCP 通过子进程解析 stdout 时直接拿不到结果。
"""

import json

from click.testing import CliRunner

from boss_agent_cli.commands import config_cmd
from boss_agent_cli.main import cli
from boss_agent_cli.output import envelope_error, envelope_success
from boss_agent_cli.services.json_files import write_json_atomic
from boss_agent_cli.services.text_files import write_text_atomic

_LONE = "\udcff"


def _envelope(result):
	payload = json.loads(result.output)
	assert payload["ok"] is False
	return payload


# ── 本地 JSON 写入 ──────────────────────────────────────────────────


def test_write_json_atomic_persists_lone_surrogate(tmp_path):
	path = tmp_path / "config.json"

	write_json_atomic(path, {"export_dir": f"x{_LONE}y"})

	# backslashreplace 写成 JSON 合法的 ``\udcff`` 转义，读回值不变。
	assert json.loads(path.read_text(encoding="utf-8")) == {"export_dir": f"x{_LONE}y"}


def test_write_text_atomic_persists_lone_surrogate(tmp_path):
	path = tmp_path / "export.html"

	write_text_atomic(path, f"<p>x{_LONE}y</p>")

	# 文本导出不是 JSON：孤立代理按字面 ``\udcff`` 转义写出，文件仍可打开。
	assert path.read_text(encoding="utf-8") == "<p>x\\udcffy</p>"


# ── 信封序列化 ──────────────────────────────────────────────────────


def test_envelope_success_encodes_lone_surrogate():
	text = envelope_success("config", {"export_dir": f"x{_LONE}y"})

	text.encode("utf-8")  # 不抛 UnicodeEncodeError
	assert json.loads(text)["data"]["export_dir"] == f"x{_LONE}y"


def test_envelope_error_encodes_lone_surrogate():
	text = envelope_error("config", code="INVALID_PARAM", message=f"坏值 {_LONE}")

	text.encode("utf-8")
	assert json.loads(text)["error"]["message"] == f"坏值 {_LONE}"


# ── 命令路径 ────────────────────────────────────────────────────────


def test_config_set_rejects_lone_surrogate_value(tmp_path):
	result = CliRunner().invoke(
		cli, ["--json", "--data-dir", str(tmp_path), "config", "set", "export_dir", f"/tmp/x{_LONE}y"]
	)

	assert result.exit_code == 1
	payload = _envelope(result)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "无法按 UTF-8 编码" in payload["error"]["message"]


def test_config_list_encodes_corrupt_config(tmp_path):
	# JSON 合法的转义序列：json.load 成功，值是孤立代理。
	(tmp_path / "config.json").write_text('{"export_dir": "x\\ud800y"}', encoding="utf-8")

	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "config", "list"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert payload["ok"] is True
	items = {item["key"]: item["value"] for item in payload["data"]["items"]}
	assert items["export_dir"] == "x\ud800y"


def test_top_level_wraps_unicode_error_as_local_io(tmp_path, monkeypatch):
	def boom(_path):
		raise UnicodeEncodeError("utf-8", _LONE, 0, 1, "surrogates not allowed")

	monkeypatch.setattr(config_cmd, "_load_user_overrides", boom)

	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "config", "list"])

	assert result.exit_code == 1
	payload = _envelope(result)
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"
	assert payload["error"]["recoverable"] is False
