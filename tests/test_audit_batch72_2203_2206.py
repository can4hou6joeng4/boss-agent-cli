"""认证与 AI 边界的四项修复（Issue #2203 / #2204 / #2205 / #2206）。"""

import json
import os
import stat
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.ai.config import AIConfigStore, AIConfigValueError, normalize_base_url
from boss_agent_cli.api.proxy import ProxyConfigurationError
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.auth.token_store import TokenStore
from boss_agent_cli.main import cli


def _clear_proxy_env(monkeypatch, value=None):
	for key in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
		monkeypatch.delenv(key, raising=False)
	if value is not None:
		monkeypatch.setenv("ALL_PROXY", value)


def _symlink_or_skip(link: Path, target: Path) -> None:
	try:
		link.symlink_to(target)
	except (OSError, NotImplementedError):
		pytest.skip("当前环境不支持创建符号链接")


# ── Issue #2203：坏代理配置不能被当成 Cookie 失效 ───────────────────────


@pytest.mark.parametrize("proxy", ["socks9://127.0.0.1:1080", "http://127.0.0.1:abc"])
def test_verify_cookie_rejects_broken_proxy_env_before_any_request(tmp_path, monkeypatch, proxy):
	_clear_proxy_env(monkeypatch, proxy)
	monkeypatch.setattr(AuthManager, "_check_cdp_login_risk_lock", lambda self, cdp_url: None)

	def explode(*args, **kwargs):
		raise AssertionError("代理配置非法时不应发出请求")

	monkeypatch.setattr("httpx.get", explode)
	manager = AuthManager.__new__(AuthManager)

	with pytest.raises(ProxyConfigurationError):
		manager._verify_cookie({"cookies": {"wt2": "x"}})


# ── Issue #2204：AI 密钥派生与登录态侧同口径 ────────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 环境变量不按 surrogateescape 解码")
def test_ai_derive_key_tolerates_surrogate_machine_id(tmp_path, monkeypatch):
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "\udcff")

	ai_key = AIConfigStore(tmp_path)._derive_key()

	assert isinstance(ai_key, bytes) and ai_key
	assert TokenStore(tmp_path / "auth")._derive_key()


# ── Issue #2205：base_url 读写两侧同一套校验 ────────────────────────────


@pytest.mark.parametrize(
	"value",
	[
		"http://exa\tmple.com/v1",
		"http://api.example .com/v1",
		"https://api.example.com/a b",
	],
)
def test_normalize_base_url_rejects_unsafe_whitespace(value):
	with pytest.raises(AIConfigValueError):
		normalize_base_url(value)


def test_normalize_base_url_keeps_valid_value():
	assert normalize_base_url("https://api.example.com/v1") == "https://api.example.com/v1"


def test_get_base_url_rejects_invalid_stored_url(tmp_path):
	ai_dir = tmp_path / "ai"
	ai_dir.mkdir(parents=True, exist_ok=True)
	(ai_dir / "config.json").write_text(
		json.dumps({"ai_base_url": "api.example.com/v1"}), encoding="utf-8"
	)

	with pytest.raises(AIConfigValueError):
		AIConfigStore(tmp_path).get_base_url()


def test_ai_local_status_reports_invalid_stored_url_as_config_invalid(tmp_path):
	ai_dir = tmp_path / "ai"
	ai_dir.mkdir(parents=True, exist_ok=True)
	(ai_dir / "config.json").write_text(
		json.dumps({"ai_base_url": "api.example.com/v1"}), encoding="utf-8"
	)

	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "ai", "local", "status"])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	# 读侧持久化配置沿用既有契约 CONFIG_INVALID（Issue #1636），
	# 与写入侧 `ai config --base-url` 的 INVALID_PARAM 分开。
	assert payload["error"]["code"] == "CONFIG_INVALID"
	assert payload["error"]["recoverable"] is True


# ── Issue #2206：收紧权限不跟随符号链接 ─────────────────────────────────


def test_token_store_skips_chmod_for_symlinked_session(tmp_path):
	target = tmp_path / "outside.bin"
	target.write_bytes(b"data")
	os.chmod(target, 0o644)
	auth = tmp_path / "auth"
	auth.mkdir()
	_symlink_or_skip(auth / "session.enc", target)

	TokenStore(auth)

	assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_token_store_tightens_regular_session(tmp_path):
	auth = tmp_path / "auth"
	auth.mkdir()
	session = auth / "session.enc"
	session.write_bytes(b"x")
	os.chmod(session, 0o644)

	TokenStore(auth)

	assert stat.S_IMODE(session.stat().st_mode) == 0o600


def test_ai_config_skips_chmod_for_symlinked_key(tmp_path):
	target = tmp_path / "outside.bin"
	target.write_bytes(b"data")
	os.chmod(target, 0o644)
	ai_dir = tmp_path / "ai"
	ai_dir.mkdir()
	_symlink_or_skip(ai_dir / "api_key.enc", target)

	AIConfigStore(tmp_path)

	assert stat.S_IMODE(target.stat().st_mode) == 0o644
