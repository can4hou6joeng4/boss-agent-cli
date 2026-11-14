"""恢复命令不得再次改写插入的数据目录，且无上下文诊断保持通用示例。"""

import json
import shlex
from types import SimpleNamespace

import pytest

from boss_agent_cli.commands._doctor_checks import _recruiter_identity_check
from boss_agent_cli.commands._filter_errors import emit_filter_error
from boss_agent_cli.display import (
	boss_command,
	browser_policy_text,
	registry_recovery_for_ctx,
	risk_error_contract,
	shell_argument,
)
from boss_agent_cli.wizard.renderer import recovery_message


@pytest.mark.parametrize(
	"directory",
	["boss doctor", "boss chat", "boss login", "boss wizard", "it's boss doctor", 'state "boss doctor"', "boss doctor \\ $STATE"],
)
def test_registry_recovery_preserves_command_words_in_the_directory(tmp_path, directory):
	data_dir = tmp_path / directory
	ctx = SimpleNamespace(obj={"platform": "zhipin", "data_dir": data_dir})
	command = registry_recovery_for_ctx(ctx, "boss hr chatmsg synthetic-id")
	assert shlex.split(command) == [
		"boss", "--data-dir", str(data_dir.resolve()), "hr", "chatmsg", "synthetic-id",
	]


def test_browser_policy_preserves_command_words_in_the_directory(tmp_path):
	data_dir = tmp_path / "boss doctor"
	command = browser_policy_text(
		"boss status --live", lambda value: boss_command(value, data_dir=data_dir)
	)
	assert shlex.split(command) == ["boss", "--data-dir", str(data_dir.resolve()), "status", "--live"]


def test_wizard_recovery_preserves_command_words_in_the_directory(tmp_path):
	data_dir = tmp_path / "boss chat"
	message = recovery_message(
		"ALREADY_APPLIED", command_for=lambda value: boss_command(value, data_dir=data_dir)
	)
	assert message is not None
	deliver_command = message.split("用 ", 1)[1].split(" 查看投递记录", 1)[0]
	assert shlex.split(deliver_command) == [
		"boss", "--data-dir", str(data_dir.resolve()), "me", "--section", "deliver",
	]
	chat_command = message.split("或用 ", 1)[1].split(" 跟进", 1)[0]
	assert shlex.split(chat_command) == ["boss", "--data-dir", str(data_dir.resolve()), "chat"]


def test_risk_contract_preserves_command_words_in_the_directory(tmp_path):
	data_dir = tmp_path / "boss doctor"
	ctx = SimpleNamespace(obj={"platform": "zhipin", "data_dir": data_dir})
	recovery, _hints = risk_error_contract("ACCOUNT_RISK", ctx=ctx)
	clean_command = recovery.split("由用户执行 ", 1)[1].split(" 并显式恢复任务", 1)[0]
	assert shlex.split(clean_command) == ["boss", "--data-dir", str(data_dir.resolve()), "clean", "--risk-lock"]


@pytest.mark.parametrize(
	("identity", "expected"),
	[
		({"ready": False, "side": "candidate"}, "在官方页面切换到「我要招聘」后执行 boss hr whoami"),
		({"ready": False, "side": "unknown"}, "执行 boss hr whoami 核对身份"),
	],
)
def test_recruiter_identity_without_context_keeps_generic_command(identity, expected):
	assert _recruiter_identity_check(identity)["recovery_action"] == expected


@pytest.mark.parametrize("directory", ["boss doctor", "it's boss doctor", 'state "boss doctor"'])
def test_registry_recovery_preserves_an_existing_command_path(tmp_path, directory):
	data_dir = tmp_path / directory
	ctx = SimpleNamespace(obj={"platform": "zhipin", "data_dir": data_dir})
	command = boss_command("resume list", data_dir=data_dir)
	assert registry_recovery_for_ctx(ctx, command) == command


def test_registry_recovery_preserves_a_quoted_record_name(tmp_path):
	ctx = SimpleNamespace(obj={"platform": "zhipin", "data_dir": tmp_path})
	message = "未找到预设 'boss doctor'；请用 `boss doctor` 诊断"
	assert registry_recovery_for_ctx(ctx, message) == (
		f"未找到预设 'boss doctor'；请用 `boss --data-dir {tmp_path.resolve()} doctor` 诊断"
	)


@pytest.mark.parametrize(
	"message",
	[
		"预设 'sample'unit boss doctor' 不存在",
		"文件 '/synthetic/sample'unit/boss doctor/resume.json' 导入失败",
	],
)
def test_registry_recovery_preserves_apostrophes_inside_quoted_values(tmp_path, message):
	ctx = SimpleNamespace(obj={"platform": "zhipin", "data_dir": tmp_path})
	assert registry_recovery_for_ctx(ctx, message) == message


@pytest.mark.parametrize("prefix", ["预设 'sample'unit' 后执行", "it's time to run"])
def test_registry_recovery_rewrites_commands_after_an_apostrophe(tmp_path, prefix):
	ctx = SimpleNamespace(obj={"platform": "zhipin", "data_dir": tmp_path})
	message = f"{prefix} boss doctor（另见 '帮助'）"
	assert registry_recovery_for_ctx(ctx, message) == (
		f"{prefix} {boss_command('doctor', data_dir=tmp_path)}（另见 '帮助'）"
	)


def test_filter_error_preserves_an_existing_command_after_an_apostrophe(tmp_path, capsys):
	data_dir = tmp_path / "boss doctor"
	ctx = SimpleNamespace(obj={"platform": "zhipin", "data_dir": data_dir, "json": True})
	name = "sample'unit"
	remove_command = boss_command(f"preset remove {shell_argument(name)}", data_dir=data_dir)
	suffix = f"（来自 preset '{name}'，可用 {remove_command} 删除后重建）"
	with pytest.raises(SystemExit) as error:
		emit_filter_error(ctx, "search", ValueError("未知薪资范围: synthetic"), suffix)
	assert error.value.code == 1
	message = json.loads(capsys.readouterr().out)["error"]["message"]
	assert message == f"未知薪资范围: synthetic{suffix}"
	command = message.split("，可用 ", 1)[1].split(" 删除后重建", 1)[0]
	assert shlex.split(command) == [
		"boss", "--data-dir", str(data_dir.resolve()), "preset", "remove", name,
	]
