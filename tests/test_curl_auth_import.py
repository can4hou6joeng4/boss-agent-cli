import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.auth.curl_import import parse_curl_auth
from boss_agent_cli.auth.manager import AuthManager, AuthRequired
from boss_agent_cli.commands.schema import SCHEMA_DATA
from boss_agent_cli.main import cli


_CURL = "curl 'https://www.zhipin.com/wapi/test' -b 'wt2=test-session; bst=current; __zp_stoken__=test-stoken'"


def test_parse_curl_keeps_native_token_shape_only():
	result = parse_curl_auth(
		_CURL + " -H 'User-Agent: TestAgent' -H 'zp_token: stale' "
		"-H 'traceid: discard' --data-raw 'friendIds=discard'"
	)
	assert result == {
		"cookies": {"wt2": "test-session", "bst": "current", "__zp_stoken__": "test-stoken"},
		"stoken": "test-stoken", "user_agent": "TestAgent",
	}


@pytest.mark.parametrize("options", [
	"-b 'wt2=x=a==' -A TestAgent",
	"--cookie='wt2=x=a==' --user-agent=TestAgent",
	"--header='Cookie: wt2=x=a==' --header='user-agent: TestAgent'",
	"-b'wt2=x=a==' -H'user-agent: TestAgent'",
])
def test_parse_curl_supported_cookie_and_header_forms(options):
	assert parse_curl_auth(f"curl --url https://www.zhipin.com {options}") == {
		"cookies": {"wt2": "x=a=="}, "stoken": "", "user_agent": "TestAgent",
	}


def test_parse_curl_multiline_and_quoted_apostrophe():
	command = "curl https://www.zhipin.com \\\n -b 'wt2=x' \\\n -H 'user-agent: agent'\"'\"'s browser'"
	assert parse_curl_auth(command)["user_agent"] == "agent's browser"


@pytest.mark.parametrize("url", [
	"https://evil.test", "https://www.zhipin.com.evil.test", "http://www.zhipin.com",
	"https://evil.test@www.zhipin.com", "https://www.zhipin.com:8080", "https://www.zhipin.com:invalid",
])
def test_parse_curl_rejects_non_boss_request(url):
	with pytest.raises(ValueError):
		parse_curl_auth(f"curl '{url}' -b 'wt2=x'")


@pytest.mark.parametrize("command", [
	"", "echo curl -b 'wt2=x'", "curl -b 'wt2=x'", "curl https://www.zhipin.com -H",
	"curl https://www.zhipin.com -b '/private/cookies.txt'",
	"curl https://www.zhipin.com -H @/private/headers.txt",
	"curl https://www.zhipin.com -b 'not_wt2=x'", _CURL + " 'unterminated",
	_CURL + " ; touch /tmp/must-not-run", _CURL + " https://evil.test",
	_CURL + " -H 'User-Agent: value\r\nInjected: value'",
])
def test_parse_curl_invalid_input(command):
	with pytest.raises(ValueError):
		parse_curl_auth(command)


def test_import_verifies_then_uses_existing_encrypted_store(tmp_path, monkeypatch):
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "curl-test-machine")
	manager = AuthManager(tmp_path)
	with patch.object(manager, "_verify_cookie", return_value=True) as verify, patch.object(manager, "login") as login:
		result = manager.import_curl(_CURL)
	verify.assert_called_once_with(parse_curl_auth(_CURL))
	login.assert_not_called()
	assert result["_method"] == "cURL 导入"
	assert AuthManager(tmp_path).get_token() == parse_curl_auth(_CURL)
	assert b"test-session" not in (tmp_path / "auth" / "session.enc").read_bytes()


def test_import_failed_verification_does_not_replace_session(tmp_path):
	manager = AuthManager(tmp_path)
	manager._token = {"cookies": {"wt2": "existing"}}
	with patch.object(manager, "_verify_cookie", return_value=False), patch.object(manager._store, "save") as save:
		with pytest.raises(AuthRequired):
			manager.import_curl(_CURL)
	save.assert_not_called()
	assert manager.get_token()["cookies"]["wt2"] == "existing"


def test_import_unsupported_platform_does_not_verify(tmp_path):
	manager = AuthManager(tmp_path, platform="zhilian")
	with patch.object(manager, "_verify_cookie") as verify:
		with pytest.raises(ValueError):
			manager.import_curl(_CURL)
	verify.assert_not_called()


def test_login_curl_stdin_uses_auth_manager_without_disclosing_input(tmp_path):
	with patch("boss_agent_cli.commands.login.AuthManager") as auth:
		auth.return_value.import_curl.return_value = {"_method": "cURL 导入", "cookies": {"wt2": "test-session"}}
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "login", "--curl-file", "-"], input=_CURL)
		auth.return_value.import_curl.assert_called_once_with(_CURL)
		auth.return_value.login.assert_not_called()
	assert json.loads(result.output)["ok"] is True
	assert "test-session" not in result.output


@pytest.mark.parametrize("options", [["--cdp"], ["--cookie-source", "chrome"]])
def test_login_curl_conflicting_sources_are_rejected(tmp_path, options):
	with patch("boss_agent_cli.commands.login.AuthManager") as auth:
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "login", "--curl-file", "-", *options], input=_CURL)
		auth.return_value.import_curl.assert_not_called()
		auth.return_value.login.assert_not_called()
	assert json.loads(result.output)["error"]["code"] == "INVALID_PARAM"


def test_login_curl_does_not_echo_unexpected_error_or_fallback(tmp_path):
	with patch("boss_agent_cli.commands.login.AuthManager") as auth:
		auth.return_value.import_curl.side_effect = RuntimeError("test-session leaked by library")
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "login", "--curl-file", "-"], input=_CURL)
		auth.return_value.login.assert_not_called()
	assert json.loads(result.output)["ok"] is False
	assert "test-session" not in result.output
	assert "next_actions" not in json.loads(result.output)["hints"]


def test_login_curl_file_option_is_in_schema_and_reads_utf8(tmp_path):
	assert "--curl-file" in SCHEMA_DATA["commands"]["login"]["options"]
	path = tmp_path / "request.txt"
	path.write_text(_CURL, encoding="utf-8")
	with patch("boss_agent_cli.commands.login.AuthManager") as auth:
		auth.return_value.import_curl.return_value = {"_method": "cURL 导入"}
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "login", "--curl-file", str(path)])
		auth.return_value.import_curl.assert_called_once_with(_CURL)
	assert json.loads(result.output)["ok"] is True


def test_import_verifies_user_info_instead_of_replaying_curl(tmp_path):
	from boss_agent_cli.api import endpoints

	manager = AuthManager(tmp_path)
	with patch("httpx.get") as get, patch.object(manager._store, "save"):
		get.return_value.json.return_value = {"code": 0}
		manager.import_curl(_CURL + " --request POST --data-raw 'must-not-send'")
	get.assert_called_once()
	assert get.call_args.args == (endpoints.USER_INFO_URL,)
	assert "data" not in get.call_args.kwargs
	assert "must-not-send" not in str(get.call_args)


def test_parse_curl_rejects_conflicting_cookies():
	with pytest.raises(ValueError):
		parse_curl_auth(_CURL + " -H 'cookie: wt2=another-account'")


def test_parse_curl_preserves_literal_shell_text_without_execution():
	with patch("subprocess.run") as run:
		result = parse_curl_auth(_CURL + " -H 'user-agent: $(must-not-run)'")
	run.assert_not_called()
	assert result["user_agent"] == "$(must-not-run)"
