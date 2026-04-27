"""auth/token_store.py + commands/login.py 覆盖率补齐。"""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.auth.token_store import TokenStore
from boss_agent_cli.main import cli


# ══════════════ TokenStore ══════════════


def _make_store(tmp_path: Path) -> TokenStore:
	return TokenStore(tmp_path / "auth")


def test_machine_id_env_override_takes_priority(tmp_path, monkeypatch):
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "env-override-id")
	store = _make_store(tmp_path)
	assert store._get_machine_id() == "env-override-id"


@patch.dict(os.environ, {}, clear=False)
def test_machine_id_linux_reads_machine_id_file(tmp_path, monkeypatch):
	"""Linux 平台读取 /etc/machine-id。"""
	monkeypatch.delenv("BOSS_AGENT_MACHINE_ID", raising=False)
	store = _make_store(tmp_path)

	fake_content = "linux-machine-abc123\n"
	with patch("boss_agent_cli.auth.token_store.platform.system", return_value="Linux"), \
		 patch("boss_agent_cli.auth.token_store.Path") as mock_path_cls:
		fake_machine_file = MagicMock()
		fake_machine_file.exists.return_value = True
		fake_machine_file.read_text.return_value = fake_content
		mock_path_cls.return_value = fake_machine_file

		result = store._get_machine_id()
		assert result == "linux-machine-abc123"


def test_machine_id_linux_file_missing_falls_back_to_hostname(tmp_path, monkeypatch):
	"""Linux 上 /etc/machine-id 缺失时应走 hostname fallback。"""
	monkeypatch.delenv("BOSS_AGENT_MACHINE_ID", raising=False)
	store = _make_store(tmp_path)

	with patch("boss_agent_cli.auth.token_store.platform.system", return_value="Linux"), \
		 patch("boss_agent_cli.auth.token_store.Path") as mock_path_cls:
		fake_machine_file = MagicMock()
		fake_machine_file.exists.return_value = False
		mock_path_cls.return_value = fake_machine_file

		result = store._get_machine_id()
		# fallback 是 sha256 hex 字符串，长度 64
		assert len(result) == 64
		assert all(c in "0123456789abcdef" for c in result)


def test_machine_id_windows_reg_query(tmp_path, monkeypatch):
	"""Windows 平台通过 reg query 读取 MachineGuid。"""
	monkeypatch.delenv("BOSS_AGENT_MACHINE_ID", raising=False)
	store = _make_store(tmp_path)

	fake_stdout = "\r\nHKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Cryptography\r\n    MachineGuid    REG_SZ    abc-windows-guid\r\n"
	fake_result = MagicMock()
	fake_result.stdout = fake_stdout

	with patch("boss_agent_cli.auth.token_store.platform.system", return_value="Windows"), \
		 patch("boss_agent_cli.auth.token_store.shutil.which", return_value="/windows/reg.exe"), \
		 patch("boss_agent_cli.auth.token_store.subprocess.run", return_value=fake_result):
		result = store._get_machine_id()
		assert result == "abc-windows-guid"


def test_machine_id_unknown_system_uses_fingerprint_fallback(tmp_path, monkeypatch):
	"""未识别的 system 应走最终兜底（sha256 hostname+system+machine）。"""
	monkeypatch.delenv("BOSS_AGENT_MACHINE_ID", raising=False)
	store = _make_store(tmp_path)

	with patch("boss_agent_cli.auth.token_store.platform.system", return_value="UnknownOS"):
		result = store._get_machine_id()
		# 应返回 64 位 sha256 hex
		assert len(result) == 64


def test_machine_id_oserror_exception_falls_back(tmp_path, monkeypatch):
	"""底层系统调用抛 OSError 时应走兜底。"""
	monkeypatch.delenv("BOSS_AGENT_MACHINE_ID", raising=False)
	store = _make_store(tmp_path)

	with patch("boss_agent_cli.auth.token_store.platform.system", return_value="Darwin"), \
		 patch("boss_agent_cli.auth.token_store.shutil.which", return_value="/usr/sbin/ioreg"), \
		 patch("boss_agent_cli.auth.token_store.subprocess.run", side_effect=OSError("denied")):
		result = store._get_machine_id()
		# 异常被吃掉，走 fingerprint 兜底
		assert len(result) == 64


def test_save_and_load_token_round_trip(tmp_path, monkeypatch):
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "stable-test-id")
	store = _make_store(tmp_path)
	token = {"cookies": {"wt2": "abc"}, "stoken": "s1", "user_agent": "UA"}

	store.save(token)
	loaded = store.load()
	assert loaded == token


def test_load_returns_none_on_corrupted_session(tmp_path, monkeypatch):
	"""session.enc 被篡改时 decrypt 失败应返回 None 而不是抛异常。"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "stable-test-id")
	store = _make_store(tmp_path)
	store.save({"cookies": {"wt2": "x"}, "stoken": "s"})

	# 写入垃圾数据到 session.enc
	session_path = tmp_path / "auth" / "session.enc"
	session_path.write_bytes(b"garbage-not-encrypted")

	assert store.load() is None


def test_load_returns_none_when_file_missing(tmp_path):
	store = _make_store(tmp_path)
	assert store.load() is None


def test_clear_removes_session_file_only(tmp_path, monkeypatch):
	"""clear 应删除 session.enc 但保留 salt。"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "stable-id")
	store = _make_store(tmp_path)
	store.save({"cookies": {"wt2": "x"}, "stoken": "s"})

	session_path = tmp_path / "auth" / "session.enc"
	salt_path = tmp_path / "auth" / "salt"
	assert session_path.exists()
	assert salt_path.exists()

	store.clear()

	assert not session_path.exists()
	assert salt_path.exists()  # salt 保留


def test_clear_idempotent_when_file_missing(tmp_path):
	store = _make_store(tmp_path)
	# 不应抛异常
	store.clear()
	store.clear()


def test_refresh_lock_acquire_and_release(tmp_path):
	store = _make_store(tmp_path)
	lock_path = tmp_path / "auth" / "refresh.lock"

	with store.refresh_lock():
		# 进入锁期间文件应存在
		# （open 后又立刻 close，但我们在 yield 之前 close 后再执行 yield 内容；
		#  实际文件是否存在取决于实现，这里弱断言）
		pass

	# 退出后锁文件应被清理
	assert not lock_path.exists()


def test_refresh_lock_clears_even_on_exception(tmp_path):
	"""with 块内抛异常时 lock 文件仍应被清理。"""
	store = _make_store(tmp_path)
	lock_path = tmp_path / "auth" / "refresh.lock"

	with pytest.raises(RuntimeError, match="boom"):
		with store.refresh_lock():
			raise RuntimeError("boom")

	assert not lock_path.exists()


def test_refresh_lock_timeout_breaks_stale_lock(tmp_path, monkeypatch):
	"""已存在的残留 lock 应在超时后被强制释放。"""
	import boss_agent_cli.auth.token_store as ts

	monkeypatch.setattr(ts, "_LOCK_TIMEOUT", 0.05)  # 50ms 超时
	store = _make_store(tmp_path)
	lock_path = tmp_path / "auth" / "refresh.lock"

	# 预先创建一个残留 lock 文件
	lock_path.parent.mkdir(parents=True, exist_ok=True)
	lock_path.touch()
	assert lock_path.exists()

	# 即使有残留锁，也应在短时间内获取到
	with store.refresh_lock():
		pass

	# 退出后应清理
	assert not lock_path.exists()


# ══════════════ commands/login ══════════════


@patch("boss_agent_cli.commands.login.AuthManager")
def test_login_success(mock_auth_cls):
	"""正常登录成功路径。"""
	mock_auth = MagicMock()
	mock_auth.login.return_value = {
		"cookies": {"wt2": "x"}, "stoken": "s", "_method": "Cookie 提取",
	}
	mock_auth_cls.return_value = mock_auth

	runner = CliRunner()
	result = runner.invoke(cli, ["login", "--timeout", "60"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert "Cookie 提取" in parsed["data"]["message"]
	mock_auth.login.assert_called_once()


@patch("boss_agent_cli.commands.login.AuthManager")
def test_login_supports_zhilian_platform(mock_auth_cls):
	mock_auth = MagicMock()
	mock_auth.login.return_value = {
		"cookies": {"zp_token": "x"}, "user_agent": "ua", "_method": "Cookie 提取",
	}
	mock_auth_cls.return_value = mock_auth

	runner = CliRunner()
	result = runner.invoke(cli, ["--platform", "zhilian", "login"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["hints"]["next_actions"][0] == "boss --platform zhilian status — 验证登录态"
	assert parsed["hints"]["next_actions"][1] == "boss --platform zhilian search <query> — 搜索职位"
	mock_auth.login.assert_called_once()


@patch("boss_agent_cli.commands.login.AuthManager")
def test_login_connection_error_recovery_is_boss_chrome(mock_auth_cls):
	"""ConnectionError 应返回 NETWORK_ERROR + boss-chrome 恢复建议。"""
	mock_auth = MagicMock()
	mock_auth.login.side_effect = ConnectionError("can't reach chrome")
	mock_auth_cls.return_value = mock_auth

	runner = CliRunner()
	result = runner.invoke(cli, ["login"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "NETWORK_ERROR"
	assert "chrome" in parsed["error"]["recovery_action"].lower()


@patch("boss_agent_cli.commands.login.AuthManager")
def test_login_timeout_error_recovery_is_retry(mock_auth_cls):
	"""TimeoutError 应返回 NETWORK_ERROR + 重试建议。"""
	mock_auth = MagicMock()
	mock_auth.login.side_effect = TimeoutError("扫码超时")
	mock_auth_cls.return_value = mock_auth

	runner = CliRunner()
	result = runner.invoke(cli, ["login"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "NETWORK_ERROR"
	assert parsed["error"]["recovery_action"] == "boss login"


@patch("boss_agent_cli.commands.login.AuthManager")
def test_zhilian_login_timeout_uses_platform_specific_recovery(mock_auth_cls):
	mock_auth = MagicMock()
	mock_auth.login.side_effect = TimeoutError("扫码超时")
	mock_auth_cls.return_value = mock_auth

	runner = CliRunner()
	result = runner.invoke(cli, ["--platform", "zhilian", "login"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["recovery_action"] == "boss --platform zhilian login"


@patch("boss_agent_cli.commands.login.AuthManager")
def test_login_generic_exception_wrapped_as_network_error(mock_auth_cls):
	"""泛型异常也应被 wrap 成 NETWORK_ERROR，不裸泄给用户。"""
	mock_auth = MagicMock()
	mock_auth.login.side_effect = RuntimeError("some unexpected error")
	mock_auth_cls.return_value = mock_auth

	runner = CliRunner()
	result = runner.invoke(cli, ["login"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "NETWORK_ERROR"
	assert "登录失败" in parsed["error"]["message"]


@patch("boss_agent_cli.commands.login.AuthManager")
def test_zhilian_login_generic_exception_uses_platform_specific_recovery(mock_auth_cls):
	mock_auth = MagicMock()
	mock_auth.login.side_effect = RuntimeError("some unexpected error")
	mock_auth_cls.return_value = mock_auth

	runner = CliRunner()
	result = runner.invoke(cli, ["--platform", "zhilian", "login"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["recovery_action"] == "boss --platform zhilian login"


@patch("boss_agent_cli.commands.login.AuthManager")
def test_login_cdp_flag_propagates_force_cdp(mock_auth_cls):
	"""--cdp 标志应把 force_cdp=True 传到 auth.login。"""
	mock_auth = MagicMock()
	mock_auth.login.return_value = {"cookies": {}, "stoken": "", "_method": "CDP 扫码"}
	mock_auth_cls.return_value = mock_auth

	runner = CliRunner()
	result = runner.invoke(cli, ["login", "--cdp"])
	assert result.exit_code == 0
	kwargs = mock_auth.login.call_args.kwargs
	assert kwargs["force_cdp"] is True


@patch("boss_agent_cli.commands.login.AuthManager")
def test_login_cookie_source_propagates(mock_auth_cls):
	"""--cookie-source 选项应传到 auth.login。"""
	mock_auth = MagicMock()
	mock_auth.login.return_value = {"cookies": {}, "stoken": "", "_method": "Cookie 提取"}
	mock_auth_cls.return_value = mock_auth

	runner = CliRunner()
	result = runner.invoke(cli, ["login", "--cookie-source", "chrome"])
	assert result.exit_code == 0
	kwargs = mock_auth.login.call_args.kwargs
	assert kwargs["cookie_source"] == "chrome"
