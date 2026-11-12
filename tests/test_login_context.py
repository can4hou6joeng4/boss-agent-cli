"""登录恢复指引必须能在同一数据目录完成整条链路，测试不访问平台。"""

import json
import shlex
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import click
import pytest
from click.testing import CliRunner

from boss_agent_cli.auth.manager import AuthManager, AuthRequired, TokenRefreshFailed
from boss_agent_cli.auth.token_store import TokenStore
from boss_agent_cli.commands._login_errors import classify_login_error
from boss_agent_cli.display import handle_error_output, login_action_for_ctx
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.models import WorkflowPlan
from boss_agent_cli.wizard.runner import WorkflowActionError, WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore


def _follow(runner: CliRunner, action: str) -> dict:
	argv = shlex.split(action)
	assert argv[0] == "boss"
	result = runner.invoke(cli, ["--json", *argv[1:]])
	assert result.exit_code == 0, result.output
	return json.loads(result.output)


@pytest.mark.parametrize("directory", ["session", "会话 ' $HOME*"])
def test_login_recovery_chain_stays_in_original_directory_after_chdir(tmp_path, monkeypatch, directory):
	monkeypatch.chdir(tmp_path)
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "fixture-machine")
	monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "default-home"))
	data_dir = tmp_path / directory
	default_dir = Path.home() / ".boss-agent"
	TokenStore(default_dir / "auth").save({"cookies": {"wt2": "default-fixture"}})
	default_session = default_dir / "auth" / "session.enc"
	before = default_session.read_bytes()

	def fake_login(auth, **kwargs):
		assert auth.data_dir.resolve() == data_dir.resolve()
		token = {"cookies": {"wt2": "fixture-session"}, "stoken": "fixture-secondary"}
		TokenStore(auth.data_dir / "auth").save(token)
		return {**token, "_method": "离线替身"}

	def fake_platform(ctx, auth):
		assert auth.data_dir.resolve() == data_dir.resolve()
		assert auth.get_token()["cookies"]["wt2"] == "fixture-session"
		platform = MagicMock()
		platform.__enter__.return_value = platform
		platform.is_success.return_value = True
		platform.unwrap_data.return_value = {}
		return platform

	monkeypatch.setattr(AuthManager, "login", fake_login)
	monkeypatch.setattr("boss_agent_cli.commands.me.get_platform_instance", fake_platform)
	monkeypatch.setattr("boss_agent_cli.commands.status.get_platform_instance", fake_platform)
	runner = CliRunner()
	first = runner.invoke(cli, ["--data-dir", directory, "--json", "me", "--section", "user"])
	assert first.exit_code == 1, first.output
	missing = json.loads(first.output)
	assert missing["error"]["code"] == "AUTH_REQUIRED"
	login_action = missing["hints"]["next_actions"][0]
	assert missing["error"]["recovery_action"] == login_action
	assert login_action in missing["hints"]["operator_actions"][0]
	assert shlex.split(login_action) == ["boss", "--data-dir", str(data_dir.resolve()), "login"]

	elsewhere = tmp_path / "elsewhere"
	elsewhere.mkdir()
	monkeypatch.chdir(elsewhere)
	logged_in = _follow(runner, login_action)
	assert [shlex.split(action) for action in logged_in["hints"]["next_actions"]] == [
		["boss", "--data-dir", str(data_dir.resolve()), *command]
		for command in (["status"], ["search", "<query>"], ["recommend"])
	]
	status = _follow(runner, logged_in["hints"]["next_actions"][0])
	assert status["data"]["logged_in"] is True
	live = _follow(runner, status["hints"]["next_actions"][0])
	assert live["data"]["live"] is True
	assert [shlex.split(action) for action in live["hints"]["next_actions"]] == [
		["boss", "--data-dir", str(data_dir.resolve()), *command]
		for command in (["recommend"], ["search", "<关键词>"])
	]
	retried = runner.invoke(cli, ["--data-dir", str(data_dir), "--json", "me", "--section", "user"])
	assert retried.exit_code == 0, retried.output
	assert default_session.read_bytes() == before
	assert not (elsewhere / directory).exists()


@pytest.mark.parametrize("code", ["AUTH_REQUIRED", "AUTH_EXPIRED", "TOKEN_REFRESH_FAILED"])
def test_platform_auth_error_keeps_directory_and_diagnostic_fields(tmp_path, monkeypatch, code):
	data_dir = tmp_path / "诊断 ' $目录"
	platform = MagicMock()
	platform.__enter__.return_value = platform
	platform.is_success.return_value = False
	platform.parse_error.return_value = (code, "fixture-auth-error")
	monkeypatch.setattr("boss_agent_cli.commands.status.AuthManager.check_status", lambda self: {"cookies": {"wt2": "fixture"}})
	monkeypatch.setattr("boss_agent_cli.commands.status.get_platform_instance", lambda ctx, auth: platform)
	result = CliRunner().invoke(cli, ["--data-dir", str(data_dir), "--json", "status", "--live"])
	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == code
	action = payload["error"]["recovery_action"]
	assert shlex.split(action) == ["boss", "--data-dir", str(data_dir.resolve()), "login"]
	assert payload["hints"]["next_actions"] == [action]
	assert action in payload["hints"]["operator_actions"][0]
	assert payload["hints"]["auth_health"]["primary_name"] == "wt2"
	assert payload["hints"]["checks"]


@pytest.mark.parametrize("exc", [
	TimeoutError("扫码超时"),
	ConnectionError("CDP 不可用"),
	RuntimeError("expired"),
	RuntimeError("cookie 提取失败"),
	RuntimeError("Executable doesn't exist"),
	RuntimeError("offline"),
])
def test_login_failure_commands_keep_directory(tmp_path, exc):
	ctx = click.Context(click.Command("login"), obj={"platform": "otherplat", "data_dir": tmp_path / "会话 ' $目录"})
	payload = classify_login_error(exc, ctx)
	commands = payload["hints"]["next_actions"]
	boss_commands = [shlex.split(action) for action in commands if action.startswith("boss ")]
	assert boss_commands
	for argv in boss_commands:
		assert argv[:5] == ["boss", "--platform", "otherplat", "--data-dir", str(ctx.obj["data_dir"].resolve())]
	assert shlex.split(payload["recovery_action"])[-1] == "login" or payload["recovery_action"] == "patchright install chromium"


@pytest.mark.parametrize("code", ["AUTH_REQUIRED", "AUTH_EXPIRED", "TOKEN_REFRESH_FAILED"])
def test_explicit_auth_failure_operator_check_is_preserved(tmp_path, monkeypatch, code):
	ctx = click.Context(click.Command("fixture"), obj={"json_output": True, "data_dir": tmp_path})
	operator_hint = {"operator_actions": ["先在官方页面核对发送结果，不要自动重试"]}
	captured = {}
	monkeypatch.setattr("boss_agent_cli.output.emit_error", lambda *args, **kwargs: captured.update(kwargs))
	handle_error_output(ctx, "fixture", code=code, message="fixture", recoverable=False,
		recovery_action="在官方页面核对本次请求状态", hints=operator_hint)
	assert captured["recoverable"] is False
	assert captured["recovery_action"] == "在官方页面核对本次请求状态"
	assert captured["hints"] == operator_hint


@pytest.mark.parametrize("failure", ["required", "refresh", "platform-required", "expired"])
def test_workflow_auth_checkpoint_keeps_original_directory(tmp_path, failure):
	data_dir = tmp_path / "workflow ' $目录"
	context = ActionContext(data_dir=data_dir, platform="otherplat", role="candidate", logger=Logger())
	plan = WorkflowPlan(role="candidate", platform="otherplat", goal="job_search", inputs={}, requested_steps=("fixture",), mode="headless")

	def fail(context, inputs, prior):
		if failure == "required":
			raise AuthRequired("fixture-auth-required")
		if failure == "refresh":
			raise TokenRefreshFailed("fixture-refresh-failed")
		code = "AUTH_REQUIRED" if failure == "platform-required" else "AUTH_EXPIRED"
		raise WorkflowActionError(code, "fixture-auth-error", recoverable=True, recovery_action="boss login")

	with WorkflowStore(data_dir) as store:
		run = WorkflowRunner(store, {"fixture": fail}).run(plan, context, run_id="auth-context")
		stored = store.get("auth-context")
	assert run["status"] == "failed"
	action = run["error"]["recovery_action"]
	assert shlex.split(action) == ["boss", "--platform", "otherplat", "--data-dir", str(data_dir.resolve()), "login"]
	assert stored["error"]["recovery_action"] == action
	assert stored["steps"][0]["error"]["recovery_action"] == action


@pytest.mark.parametrize("token", [None, {}, {"cookies": {}}, {"cookies": {"other": "fixture"}}])
def test_crawl_login_precheck_keeps_original_directory(tmp_path, monkeypatch, token):
	context = ActionContext(data_dir=tmp_path / "采集 ' $目录", platform="zhipin", role="candidate", logger=Logger(), config={"crawl": {"cdp_port": 9222}})

	def get_token(self):
		if token is None:
			raise AuthRequired("fixture-auth-required")
		return token

	monkeypatch.setattr(AuthManager, "get_token", get_token)
	with pytest.raises(WorkflowActionError) as caught:
		DEFAULT_ACTIONS["crawl_start"](context, {"query": "Python", "city": "杭州"}, {})
	assert caught.value.code == "AUTH_REQUIRED"
	assert shlex.split(caught.value.recovery_action) == ["boss", "--data-dir", str(context.data_dir.resolve()), "login"]


def test_waiting_login_can_resume_and_check_original_workflow(tmp_path, monkeypatch):
	data_dir = tmp_path / "等待 ' $会话"
	default_dir = tmp_path / "default-home" / ".boss-agent"
	monkeypatch.setattr(Path, "home", classmethod(lambda cls: default_dir.parent))
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "fixture-machine")
	plan = WorkflowPlan(role="candidate", platform="zhipin", goal="recommendations", inputs={}, requested_steps=("auth_status",), mode="headless")
	context = ActionContext(data_dir=data_dir, platform="zhipin", role="candidate", logger=Logger())
	run_id = "waiting; fixture"
	with WorkflowStore(default_dir) as store:
		store.create(run_id, plan)
	with WorkflowStore(data_dir) as store:
		waiting = WorkflowRunner(store, DEFAULT_ACTIONS).run(plan, context, run_id=run_id)
	assert waiting["status"] == "waiting_input"
	last = waiting["last_result"]
	login_action = login_action_for_ctx(SimpleNamespace(obj={"data_dir": data_dir}))
	assert login_action in last["operator_actions"][0]
	assert shlex.split(last["next_action"]) == ["boss", "--data-dir", str(data_dir.resolve()), "wizard", "--resume", run_id]

	def fake_login(auth, **kwargs):
		TokenStore(auth.data_dir / "auth").save({"cookies": {"wt2": "fixture-session"}, "stoken": "fixture-secondary"})
		return {"_method": "离线替身"}

	monkeypatch.setattr(AuthManager, "login", fake_login)
	monkeypatch.chdir(tmp_path)
	runner = CliRunner()
	_follow(runner, login_action)
	resumed = _follow(runner, last["next_action"])
	assert resumed["data"]["status"] == "completed"
	checked = _follow(runner, resumed["hints"]["next_actions"][0])
	assert checked["data"]["run_id"] == run_id
	assert checked["data"]["status"] == "completed"
	assert all(shlex.split(action)[:3] == ["boss", "--data-dir", str(data_dir.resolve())] for action in checked["hints"]["next_actions"])
	with WorkflowStore(default_dir) as store:
		assert store.get(run_id)["status"] == "pending"
	assert not (default_dir / "auth" / "session.enc").exists()


def test_login_action_keeps_platform_and_directory(tmp_path):
	ctx = SimpleNamespace(obj={"platform": "otherplat", "data_dir": tmp_path / "目录 ' $HOME*"})
	assert shlex.split(login_action_for_ctx(ctx)) == [
		"boss", "--platform", "otherplat", "--data-dir", str(ctx.obj["data_dir"].resolve()), "login",
	]
