"""显式空值与会话密钥清除的回归测试（Issue #1499 / #1500）。

`ai config` 曾用真值判断「是否提供了更新」：`--model ""` 被当成没传、退回查看
模式；`--api-key ""` 却会把已存密钥覆盖成空串，而 `api_key_set` 仍报 true。
`ai local configure` 的 `--model ""` 会被保存，`--base-url ""` 则被 `or` 静默
回退到运行时默认端点。这里覆盖四类显式空值、密钥清除与本地配置不被半覆盖。
"""

import json

import pytest
from click.testing import CliRunner

from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.main import cli


def _invoke(tmp_path, group: tuple[str, ...], *args):
	return CliRunner().invoke(
		cli, ["--json", "--data-dir", str(tmp_path), *group, *args]
	)


def _ai_config(tmp_path, *args):
	return _invoke(tmp_path, ("ai", "config"), *args)


def _ai_local(tmp_path, *args):
	return _invoke(tmp_path, ("ai", "local"), *args)


@pytest.fixture
def configured_store(tmp_path, monkeypatch):
	"""一份可用的 AI 配置与已存密钥；显式空值不得改动其中任何一项。"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	store = AIConfigStore(tmp_path)
	store.save_config(
		ai_provider="openai",
		ai_model="gpt-4o",
		ai_base_url="https://proxy.internal/v1",
	)
	store.save_api_key("sk-existing")
	return store


@pytest.mark.parametrize(
	"option,value",
	[
		("--provider", ""),
		("--provider", "   "),
		("--model", ""),
		("--model", "   "),
		("--base-url", ""),
		("--base-url", "   "),
		("--api-key", ""),
		("--api-key", "   "),
	],
)
def test_ai_config_explicit_empty_strings_are_rejected_before_writing(
	tmp_path, configured_store, option, value
):
	"""四类字符串选项的显式空值都必须 INVALID_PARAM，且配置与密钥原样保留。"""
	before = configured_store.load_config()

	result = _ai_config(tmp_path, option, value)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert configured_store.load_config() == before
	assert configured_store.get_api_key() == "sk-existing"


def test_ai_config_empty_api_key_never_clears_stored_key(tmp_path, configured_store):
	"""Issue #1499 的原始复现：带空 --api-key 的更新不得把已存密钥写成空串。"""
	result = _ai_config(tmp_path, "--provider", "openai", "--model", "gpt-4o", "--api-key", "")

	assert result.exit_code == 1, result.output
	assert json.loads(result.output)["error"]["code"] == "INVALID_PARAM"
	assert configured_store.get_api_key() == "sk-existing"

	view = json.loads(_ai_config(tmp_path).output)
	assert view["data"]["api_key_set"] is True


def test_ai_config_clear_api_key_removes_credential_and_reports_not_set(
	tmp_path, configured_store
):
	"""清除凭据必须走显式 --clear-api-key，之后查看模式报 api_key_set=false。"""
	result = _ai_config(tmp_path, "--clear-api-key")

	assert result.exit_code == 0, result.output
	data = json.loads(result.output)["data"]
	assert data["action"] == "update"
	assert "api_key" in data["updated_fields"]
	assert data["api_key_set"] is False
	assert configured_store.get_api_key() is None
	assert not (tmp_path / "ai" / "api_key.enc").exists()

	view = json.loads(_ai_config(tmp_path).output)
	assert view["data"]["api_key_set"] is False


def test_ai_config_rejects_api_key_together_with_clear(tmp_path, configured_store):
	"""设置与清除是互斥语义，同时传入时不能先写后删。"""
	result = _ai_config(tmp_path, "--api-key", "sk-new", "--clear-api-key")

	assert result.exit_code == 1, result.output
	assert json.loads(result.output)["error"]["code"] == "INVALID_PARAM"
	assert configured_store.get_api_key() == "sk-existing"


def test_ai_config_and_local_status_treat_empty_stored_key_as_unset(tmp_path, monkeypatch):
	"""旧版本写下的空串密钥不是「已配置」：查询接口与 configured 必须一致。"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	store = AIConfigStore(tmp_path)
	store.save_config(ai_provider="openai", ai_model="gpt-4o")
	store.save_api_key("")

	assert store.get_api_key() == ""
	assert store.api_key_is_set() is False
	assert store.is_configured() is False

	config_view = json.loads(_ai_config(tmp_path).output)["data"]
	assert config_view["api_key_set"] is False

	local_status = json.loads(_ai_local(tmp_path, "status").output)["data"]
	assert local_status["api_key_set"] is False
	assert local_status["configured"] is False


@pytest.mark.parametrize("model", ["", "   "])
def test_ai_local_configure_empty_model_does_not_overwrite_config(tmp_path, monkeypatch, model):
	"""空 --model 曾让 `ai local status` 变 configured=false；现在写盘前拒绝。"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	store = AIConfigStore(tmp_path)
	store.save_config(
		ai_provider="ollama",
		ai_model="qwen3:14b",
		ai_base_url="http://localhost:11434/v1",
	)
	store.save_api_key("local")
	before = store.load_config()

	result = _ai_local(tmp_path, "configure", "--runtime", "ollama", "--model", model)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "--model" in payload["error"]["message"]
	assert store.load_config() == before

	status = json.loads(_ai_local(tmp_path, "status").output)["data"]
	assert status["configured"] is True
	assert status["model"] == "qwen3:14b"


@pytest.mark.parametrize("base_url", ["", "   "])
def test_ai_local_configure_empty_base_url_does_not_fall_back(tmp_path, monkeypatch, base_url):
	"""空 --base-url 曾静默回退到 runtime 默认端点；现在拒绝且不改动任何字段。"""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	store = AIConfigStore(tmp_path)
	store.save_config(
		ai_provider="vllm",
		ai_model="old-model",
		ai_base_url="http://127.0.0.1:9999/v1",
	)
	store.save_api_key("local")
	before = store.load_config()

	result = _ai_local(
		tmp_path,
		"configure",
		"--runtime", "vllm",
		"--model", "new-model",
		"--base-url", base_url,
	)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "--base-url" in payload["error"]["message"]
	assert store.load_config() == before
	assert store.get_base_url() == "http://127.0.0.1:9999/v1"


def test_ai_local_configure_rejects_empty_runtime(tmp_path):
	"""--runtime 是 Choice，显式空值由 Click 按参数错误拒绝。"""
	result = _ai_local(tmp_path, "configure", "--runtime", "", "--model", "qwen3:14b")

	assert result.exit_code == 1, result.output
	assert json.loads(result.output)["error"]["code"] == "INVALID_PARAM"
	assert AIConfigStore(tmp_path).load_config()["ai_model"] is None
