"""Tests for AI configuration store."""

from boss_agent_cli.ai.config import AIConfigStore, PROVIDER_BASE_URLS


def _make_store(tmp_path, monkeypatch) -> AIConfigStore:
	"""Create a store with stable machine ID."""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine-id")
	return AIConfigStore(tmp_path)


# ── API key encryption ───────────────────────────────────────


def test_api_key_roundtrip(tmp_path, monkeypatch):
	"""Saved API key can be loaded back."""
	store = _make_store(tmp_path, monkeypatch)
	store.save_api_key("sk-test-key-12345")
	assert store.get_api_key() == "sk-test-key-12345"


def test_api_key_not_set(tmp_path, monkeypatch):
	"""Returns None when no API key is saved."""
	store = _make_store(tmp_path, monkeypatch)
	assert store.get_api_key() is None


def test_api_key_overwrite(tmp_path, monkeypatch):
	"""Overwriting API key works."""
	store = _make_store(tmp_path, monkeypatch)
	store.save_api_key("old-key")
	store.save_api_key("new-key")
	assert store.get_api_key() == "new-key"


def test_api_key_different_machine_id(tmp_path, monkeypatch):
	"""Different machine_id cannot decrypt the key."""
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "machine-a")
	store_a = AIConfigStore(tmp_path)
	store_a.save_api_key("secret-key")

	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "machine-b")
	store_b = AIConfigStore(tmp_path)
	assert store_b.get_api_key() is None


# ── config save/load ─────────────────────────────────────────


def test_config_save_and_load(tmp_path, monkeypatch):
	"""Config can be saved and loaded."""
	store = _make_store(tmp_path, monkeypatch)
	store.save_config(ai_provider="openai", ai_model="gpt-4")
	config = store.load_config()
	assert config["ai_provider"] == "openai"
	assert config["ai_model"] == "gpt-4"


def test_config_defaults(tmp_path, monkeypatch):
	"""Default config values are returned when nothing is saved."""
	store = _make_store(tmp_path, monkeypatch)
	config = store.load_config()
	assert config["ai_provider"] is None
	assert config["ai_model"] is None
	assert config["ai_base_url"] is None
	assert config["ai_temperature"] == 0.7
	assert config["ai_max_tokens"] == 4096


def test_config_partial_update(tmp_path, monkeypatch):
	"""Partial updates merge with existing config."""
	store = _make_store(tmp_path, monkeypatch)
	store.save_config(ai_provider="openai")
	store.save_config(ai_model="gpt-4o")
	config = store.load_config()
	assert config["ai_provider"] == "openai"
	assert config["ai_model"] == "gpt-4o"


def test_config_preserves_defaults_on_partial(tmp_path, monkeypatch):
	"""Unset keys keep their defaults after partial save."""
	store = _make_store(tmp_path, monkeypatch)
	store.save_config(ai_provider="deepseek")
	config = store.load_config()
	assert config["ai_temperature"] == 0.7
	assert config["ai_max_tokens"] == 4096


# ── base_url ─────────────────────────────────────────────────


def test_base_url_provider_lookup(tmp_path, monkeypatch):
	"""Base URL is resolved from PROVIDER_BASE_URLS when not explicitly set."""
	store = _make_store(tmp_path, monkeypatch)
	store.save_config(ai_provider="openai")
	assert store.get_base_url() == "https://api.openai.com/v1"


def test_base_url_user_override(tmp_path, monkeypatch):
	"""Explicit ai_base_url overrides provider lookup."""
	store = _make_store(tmp_path, monkeypatch)
	store.save_config(ai_provider="openai", ai_base_url="https://my-proxy.com/v1")
	assert store.get_base_url() == "https://my-proxy.com/v1"


def test_base_url_custom_provider(tmp_path, monkeypatch):
	"""Custom provider returns None base_url when no explicit URL set."""
	store = _make_store(tmp_path, monkeypatch)
	store.save_config(ai_provider="custom")
	assert store.get_base_url() is None


def test_base_url_deepseek(tmp_path, monkeypatch):
	"""Deepseek provider returns correct URL."""
	store = _make_store(tmp_path, monkeypatch)
	store.save_config(ai_provider="deepseek")
	assert store.get_base_url() == "https://api.deepseek.com/v1"


def test_base_url_moonshot(tmp_path, monkeypatch):
	"""Moonshot provider returns correct URL."""
	store = _make_store(tmp_path, monkeypatch)
	store.save_config(ai_provider="moonshot")
	assert store.get_base_url() == "https://api.moonshot.cn/v1"


def test_base_url_openrouter(tmp_path, monkeypatch):
	"""OpenRouter 聚合入口，支持 Claude / GPT-5 等多家模型。"""
	store = _make_store(tmp_path, monkeypatch)
	store.save_config(ai_provider="openrouter")
	assert store.get_base_url() == "https://openrouter.ai/api/v1"


def test_base_url_qwen(tmp_path, monkeypatch):
	"""通义千问 DashScope OpenAI 兼容入口。"""
	store = _make_store(tmp_path, monkeypatch)
	store.save_config(ai_provider="qwen")
	assert store.get_base_url() == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_base_url_zhipu(tmp_path, monkeypatch):
	"""智谱 GLM 开放平台入口。"""
	store = _make_store(tmp_path, monkeypatch)
	store.save_config(ai_provider="zhipu")
	assert store.get_base_url() == "https://open.bigmodel.cn/api/paas/v4"


def test_base_url_siliconflow(tmp_path, monkeypatch):
	"""硅基流动聚合推理入口。"""
	store = _make_store(tmp_path, monkeypatch)
	store.save_config(ai_provider="siliconflow")
	assert store.get_base_url() == "https://api.siliconflow.cn/v1"


# ── is_configured ────────────────────────────────────────────


def test_is_configured_complete(tmp_path, monkeypatch):
	"""is_configured returns True when all required settings are present."""
	store = _make_store(tmp_path, monkeypatch)
	store.save_config(ai_provider="openai", ai_model="gpt-4")
	store.save_api_key("sk-test-key")
	assert store.is_configured() is True


def test_is_configured_missing_key(tmp_path, monkeypatch):
	"""is_configured returns False when API key is missing."""
	store = _make_store(tmp_path, monkeypatch)
	store.save_config(ai_provider="openai", ai_model="gpt-4")
	assert store.is_configured() is False


def test_is_configured_missing_provider(tmp_path, monkeypatch):
	"""is_configured returns False when provider is missing."""
	store = _make_store(tmp_path, monkeypatch)
	store.save_config(ai_model="gpt-4")
	store.save_api_key("sk-test-key")
	assert store.is_configured() is False


def test_is_configured_missing_model(tmp_path, monkeypatch):
	"""is_configured returns False when model is missing."""
	store = _make_store(tmp_path, monkeypatch)
	store.save_config(ai_provider="openai")
	store.save_api_key("sk-test-key")
	assert store.is_configured() is False


# ── provider base URLs ───────────────────────────────────────


def test_provider_base_urls_completeness():
	"""All expected providers are in the map."""
	assert "openai" in PROVIDER_BASE_URLS
	assert "deepseek" in PROVIDER_BASE_URLS
	assert "moonshot" in PROVIDER_BASE_URLS
	assert "openrouter" in PROVIDER_BASE_URLS
	assert "qwen" in PROVIDER_BASE_URLS
	assert "zhipu" in PROVIDER_BASE_URLS
	assert "siliconflow" in PROVIDER_BASE_URLS
	assert "custom" in PROVIDER_BASE_URLS
	assert PROVIDER_BASE_URLS["custom"] is None
