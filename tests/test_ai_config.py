"""Tests for AI configuration store."""

from boss_agent_cli.ai.config import AIConfigStore, PROVIDER_BASE_URLS
from boss_agent_cli.ai.local_models import RUNTIME_BASE_URLS

#: registry 的期望全集。新增 / 移除 provider 时必须同步这里——这正是让
#: 「移除」这件事无法静默发生的那道门禁，见 test_provider_registry_matches_the_declared_set_exactly。
_EXPECTED_PROVIDERS = frozenset({
	"openai",
	"deepseek",
	"moonshot",
	"openrouter",
	"qwen",
	"zhipu",
	"siliconflow",
	"atlas",
	"ollama",
	"vllm",
	"custom",
})


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


def test_base_url_atlas(tmp_path, monkeypatch):
	"""Atlas Cloud 全模态聚合入口。"""
	store = _make_store(tmp_path, monkeypatch)
	store.save_config(ai_provider="atlas")
	assert store.get_base_url() == "https://api.atlascloud.ai/v1"


def test_base_url_ollama(tmp_path, monkeypatch):
	"""Ollama 本地 OpenAI 兼容入口。"""
	store = _make_store(tmp_path, monkeypatch)
	store.save_config(ai_provider="ollama")
	assert store.get_base_url() == "http://localhost:11434/v1"


def test_base_url_vllm(tmp_path, monkeypatch):
	"""vLLM 本地/内网 OpenAI 兼容入口。"""
	store = _make_store(tmp_path, monkeypatch)
	store.save_config(ai_provider="vllm")
	assert store.get_base_url() == "http://localhost:8000/v1"


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


def test_provider_registry_matches_the_declared_set_exactly():
	"""registry 必须与下面这份清单**逐名相等**——多一个少一个都要红。

	此前这条测试全是 `in` 断言、没有全集断言，于是**删掉一个 provider 条目
	连同它那行断言，套件照样绿**。`atlas` 就是这样在一次无关重构里被静默删掉、
	很久之后才有人发现的。

	`CONTRIBUTING.md` 的「新增 AI provider」第 3 条允许直接移除失效端点、
	不走弃用周期——那条规则成立的前提是**移除这件事必须被看见**。这条测试
	就是那个前提。
	"""
	actual = set(PROVIDER_BASE_URLS)
	removed = _EXPECTED_PROVIDERS - actual
	added = actual - _EXPECTED_PROVIDERS

	assert not removed, (
		f"provider 被移除：{sorted(removed)}。\n"
		"移除是允许的（CONTRIBUTING「新增 AI provider」第 3 条），但必须是有意的：\n"
		"  1. 从本文件的 _EXPECTED_PROVIDERS 里删掉\n"
		"  2. 同步 ai_cmd.py 的 --provider help 文案与 docs/integrations/ai-models.md（中英两份）\n"
		"  3. 在 CHANGELOG 的 [Unreleased] 记一笔——用户的 config 里可能还存着这个值"
	)
	assert not added, (
		f"新增 provider：{sorted(added)}。\n"
		"请把它加进本文件的 _EXPECTED_PROVIDERS，并走完 CONTRIBUTING「新增 AI provider」\n"
		"的五处连锁更新（config.py / ai_cmd.py help / ai-models 中英两份 / 本文件 / CHANGELOG）。"
	)


def test_local_runtimes_stay_in_sync_with_the_provider_registry():
	"""`RUNTIME_BASE_URLS` 的每一项都必须在 registry 里且 URL 一致。

	两张表各存了一份 ollama / vllm 的地址。`automation/reply_ai.py` 用
	`frozenset(RUNTIME_BASE_URLS)` 判断「这个 provider 是不是本地模型」，
	两表漂移会让自动回复的「仅本地」约束静默失效。
	"""
	for runtime, url in RUNTIME_BASE_URLS.items():
		assert runtime in PROVIDER_BASE_URLS, f"本地运行时 {runtime} 不在 PROVIDER_BASE_URLS 里"
		assert PROVIDER_BASE_URLS[runtime] == url, (
			f"{runtime} 在两张表里的地址不一致："
			f"PROVIDER_BASE_URLS={PROVIDER_BASE_URLS[runtime]!r} vs RUNTIME_BASE_URLS={url!r}"
		)


def test_provider_base_url_shape():
	"""除 `custom` 外每个条目都是形状合法、无尾斜杠的 http(s) 地址。"""
	assert PROVIDER_BASE_URLS["custom"] is None, "custom 必须为 None —— 它要求用户显式给 --base-url"

	for name, url in PROVIDER_BASE_URLS.items():
		if name == "custom":
			continue
		assert isinstance(url, str) and url, f"{name} 的 base_url 为空"
		assert url.startswith(("http://", "https://")), f"{name} 的 base_url 不是 http(s)：{url!r}"
		assert not url.endswith("/"), f"{name} 的 base_url 带尾斜杠，与表内其余条目不一致：{url!r}"
