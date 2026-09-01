"""`boss ai config` 命令级测试。

这条命令此前零测试覆盖。核心断言是**解析后的实际端点必须回显**：
只设 `--provider` 时 `ai_base_url` 为空，真正生效的地址来自 `PROVIDER_BASE_URLS`
查表；不回显的话，provider 名相近（`openrouter` / `orcarouter`）记混会把 API key
与简历全文发到另一家，而用户在任何界面上都看不出差别。
"""

import json

from click.testing import CliRunner

from boss_agent_cli.ai.config import PROVIDER_BASE_URLS, AIConfigStore
from boss_agent_cli.main import cli


def _run(tmp_path, *args):
	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "ai", "config", *args])
	assert result.exit_code == 0, result.output
	return json.loads(result.stdout)


def test_view_echoes_resolved_base_url_for_a_named_provider(tmp_path):
	"""只设 provider（不设 --base-url）时，查看模式必须回显查表得到的端点。"""
	store = AIConfigStore(tmp_path)
	store.save_config(ai_provider="openrouter", ai_model="anthropic/claude-sonnet-4.5")

	payload = _run(tmp_path)

	assert payload["ok"] is True
	data = payload["data"]
	# 用户从没设过 ai_base_url —— 正是这种情况下端点最不透明
	assert not data.get("ai_base_url")
	assert data["resolved_base_url"] == PROVIDER_BASE_URLS["openrouter"]


def test_view_resolved_base_url_prefers_explicit_override(tmp_path):
	"""显式 --base-url 优先于 provider 查表，回显的必须是真正生效的那个。"""
	store = AIConfigStore(tmp_path)
	store.save_config(
		ai_provider="openrouter",
		ai_model="m",
		ai_base_url="https://proxy.internal/v1",
	)

	data = _run(tmp_path)["data"]

	assert data["resolved_base_url"] == "https://proxy.internal/v1"


def test_view_resolved_base_url_is_null_when_unconfigured(tmp_path):
	"""全新 data-dir 下没有可解析的端点，字段仍存在且为 null（不缺键）。"""
	data = _run(tmp_path)["data"]

	assert "resolved_base_url" in data
	assert data["resolved_base_url"] is None


def test_view_never_leaks_the_api_key(tmp_path):
	"""回显端点不得顺带把密钥带出来——只暴露布尔。"""
	store = AIConfigStore(tmp_path)
	store.save_config(ai_provider="openai", ai_model="gpt-4o")
	store.save_api_key("sk-super-secret")

	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "ai", "config"])
	data = json.loads(result.stdout)["data"]

	assert data["api_key_set"] is True
	assert "ai_api_key" not in data
	assert "sk-super-secret" not in result.stdout


def test_update_echoes_resolved_base_url_immediately(tmp_path):
	"""写入后同一屏内就能确认「这个 provider 会把数据发去哪」。"""
	data = _run(tmp_path, "--provider", "deepseek", "--model", "deepseek-chat")["data"]

	assert data["action"] == "update"
	assert set(data["updated_fields"]) == {"ai_provider", "ai_model"}
	assert data["resolved_base_url"] == PROVIDER_BASE_URLS["deepseek"]


def test_brand_adjacent_providers_resolve_to_different_endpoints(tmp_path):
	"""名字相近的 provider 必须解析到不同端点，且该差别在输出里可见。

	`--provider` 没有 click.Choice 校验（自由字符串），所以拼错不会报错；
	能救用户的只有这条回显。
	"""
	adjacent = [name for name in PROVIDER_BASE_URLS if name.endswith("router")]
	if len(adjacent) < 2:
		return  # 仓库里目前只有一家 *router 时跳过

	seen = set()
	for name in adjacent:
		data = _run(tmp_path, "--provider", name, "--model", "m")["data"]
		seen.add(data["resolved_base_url"])
	assert len(seen) == len(adjacent), f"{adjacent} 解析到了相同端点，用户无法区分"


def test_unknown_provider_resolves_to_null_rather_than_a_wrong_endpoint(tmp_path):
	"""拼错的 provider 解析为 null，不会静默落到别家端点。"""
	data = _run(tmp_path, "--provider", "openrouterr", "--model", "m")["data"]

	assert data["resolved_base_url"] is None
