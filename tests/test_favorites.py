"""favorites list/sync 命令测试。

参考 test_new_commands.py 的 history 测试组（@patch 装饰器栈 + _ctx_mock），
而非 test_shortlist.py 的纯本地模式——favorites 需要调 platform.job_favorites。
"""
import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli


def _ctx_mock(mock_cls):
	"""mock get_platform_instance 返回的 platform 实例（context manager）。"""
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") if "zpData" in response else response.get("data")
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


def _make_card(sid="sec_test1", jid="job_test1", name="示例职位", company="示例公司", city="北京", salary="25-40K", labels=None, drop=()):
	"""构造 geekGetJob card（字段名以 mitmproxy 实测为准；drop 可删除指定 key 模拟脏数据）。"""
	card = {
		"securityId": sid,
		"encryptJobId": jid,
		"jobName": name,
		"brandName": company,
		"cityName": city,
		"jobSalary": salary,
		"jobLabels": labels if labels is not None else ["双休"],
	}
	for key in drop:
		card.pop(key, None)
	return card


def _shortlist_items(tmp_path):
	"""读取本地候选池（shortlist list 为纯本地命令，不触平台）。"""
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "shortlist", "list"])
	assert result.exit_code == 0
	return json.loads(result.output)["data"]


# ── list ──────────────────────────────────────────────────────────


@patch("boss_agent_cli.commands.favorites.get_platform_instance")
@patch("boss_agent_cli.commands.favorites.AuthManager")
def test_favorites_list_success(mock_auth_cls, mock_platform_cls):
	platform = _ctx_mock(mock_platform_cls)
	platform.job_favorites.return_value = {
		"code": 0,
		"zpData": {"cardList": [_make_card()], "hasMore": False},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "favorites", "list"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert len(parsed["data"]) == 1
	item = parsed["data"][0]
	# list 预览脱敏 security_id/job_id（issue #354 契约 + 维护者要求），完整值落库后从 shortlist list 取
	assert item["security_id"] == "[REDACTED]"
	assert item["job_id"] == "[REDACTED]"
	assert item["title"] == "示例职位"
	assert item["company"] == "示例公司"
	assert item["salary"] == "25-40K"
	assert item["source"] == "favorites"
	assert parsed["pagination"]["has_more"] is False
	platform.job_favorites.assert_called_once_with(page=1, tag=4, is_active=True)


@patch("boss_agent_cli.commands.favorites.get_platform_instance")
@patch("boss_agent_cli.commands.favorites.AuthManager")
def test_favorites_list_hints_avoid_detail_with_redacted_sid(mock_auth_cls, mock_platform_cls):
	"""list 脱敏 sid，hints 须引导 sync→--json shortlist list→detail --job-id，不得退回裸 detail <sid>。"""
	platform = _ctx_mock(mock_platform_cls)
	platform.job_favorites.return_value = {
		"code": 0,
		"zpData": {"cardList": [_make_card()], "hasMore": False},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "favorites", "list"])
	assert result.exit_code == 0
	next_actions = json.loads(result.output)["hints"]["next_actions"]
	joined = " ".join(next_actions)
	# 链路三要素：sync 落库 → --json shortlist list 取 id（--json 必须前置）→ detail 带 --job-id
	assert "favorites sync" in joined
	assert "--json shortlist list" in joined
	assert "--job-id" in joined
	# 不得有"裸 detail <security_id>"（无 --job-id）的整条，锁死不退回误导版
	import re
	assert not any(re.search(r"detail <security_id>(?! --job-id)", a) for a in next_actions)


# ── sync ──────────────────────────────────────────────────────────


@patch("boss_agent_cli.commands.favorites.get_platform_instance")
@patch("boss_agent_cli.commands.favorites.AuthManager")
def test_favorites_sync_imports_and_persists(mock_auth_cls, mock_platform_cls, tmp_path):
	platform = _ctx_mock(mock_platform_cls)
	platform.job_favorites.return_value = {
		"code": 0,
		"zpData": {
			"cardList": [
				_make_card(sid="sec_a", jid="job_a"),
				_make_card(sid="sec_b", jid="job_b", name="示例职位B"),
			],
			"hasMore": False,
		},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "favorites", "sync"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["imported_count"] == 2
	assert parsed["data"]["existing_count"] == 0
	assert parsed["data"]["skipped_count"] == 0
	items = _shortlist_items(tmp_path)
	assert len(items) == 2
	assert all(item["source"] == "favorites" for item in items)


@patch("boss_agent_cli.commands.favorites.get_platform_instance")
@patch("boss_agent_cli.commands.favorites.AuthManager")
def test_favorites_sync_multi_page(mock_auth_cls, mock_platform_cls, tmp_path):
	platform = _ctx_mock(mock_platform_cls)

	def effect(*args, **kwargs):
		page = kwargs.get("page", 1)
		if page == 1:
			return {"code": 0, "zpData": {"cardList": [_make_card(sid="sec_a", jid="job_a")], "hasMore": True}}
		if page == 2:
			return {"code": 0, "zpData": {"cardList": [_make_card(sid="sec_b", jid="job_b")], "hasMore": False}}
		return {"code": 0, "zpData": {"cardList": [], "hasMore": False}}

	platform.job_favorites.side_effect = effect
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "favorites", "sync"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["imported_count"] == 2
	assert len(_shortlist_items(tmp_path)) == 2


@patch("boss_agent_cli.commands.favorites.get_platform_instance")
@patch("boss_agent_cli.commands.favorites.AuthManager")
def test_favorites_sync_middle_page_failure_no_persist(mock_auth_cls, mock_platform_cls, tmp_path):
	platform = _ctx_mock(mock_platform_cls)
	platform.parse_error.return_value = ("RATE_LIMITED", "too fast")

	def effect(*args, **kwargs):
		page = kwargs.get("page", 1)
		if page == 1:
			return {"code": 0, "zpData": {"cardList": [_make_card(sid="sec_a", jid="job_a")], "hasMore": True}}
		return {"code": 9, "message": "too fast"}

	platform.job_favorites.side_effect = effect
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "favorites", "sync"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "RATE_LIMITED"
	# 任一页失败 = 全部丢弃，不部分落库
	assert len(_shortlist_items(tmp_path)) == 0


@patch("boss_agent_cli.commands.favorites.get_platform_instance")
@patch("boss_agent_cli.commands.favorites.AuthManager")
def test_favorites_sync_existing_skipped_preserves_created_at(mock_auth_cls, mock_platform_cls, tmp_path):
	runner = CliRunner()
	# 先手动加入候选池（纯本地，不触平台）
	runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "shortlist", "add", "sec_a", "job_a", "--title", "旧标题", "--source", "manual"],
	)
	original_created_at = _shortlist_items(tmp_path)[0]["created_at"]

	# sync 同一职位
	platform = _ctx_mock(mock_platform_cls)
	platform.job_favorites.return_value = {
		"code": 0,
		"zpData": {"cardList": [_make_card(sid="sec_a", jid="job_a", name="新标题")], "hasMore": False},
	}
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "favorites", "sync"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["imported_count"] == 0
	assert parsed["data"]["existing_count"] == 1
	after = _shortlist_items(tmp_path)
	assert len(after) == 1
	# INSERT OR IGNORE 不更新已有行，created_at 保留
	assert after[0]["created_at"] == original_created_at


@patch("boss_agent_cli.commands.favorites.get_platform_instance")
@patch("boss_agent_cli.commands.favorites.AuthManager")
def test_favorites_sync_dirty_card_missing_keys_skipped(mock_auth_cls, mock_platform_cls, tmp_path):
	platform = _ctx_mock(mock_platform_cls)
	dirty = {"jobName": "无ID职位"}  # 缺 securityId/encryptJobId
	platform.job_favorites.return_value = {
		"code": 0,
		"zpData": {"cardList": [dirty, _make_card(sid="sec_a", jid="job_a")], "hasMore": False},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "favorites", "sync"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["imported_count"] == 1
	assert parsed["data"]["skipped_count"] == 1
	assert len(_shortlist_items(tmp_path)) == 1


@patch("boss_agent_cli.commands.favorites.get_platform_instance")
@patch("boss_agent_cli.commands.favorites.AuthManager")
def test_favorites_sync_notnull_field_fallback(mock_auth_cls, mock_platform_cls, tmp_path):
	platform = _ctx_mock(mock_platform_cls)
	# 缺 jobName/brandName/cityName/jobSalary（NOT NULL 字段）→ _card_to_shortlist_item 兜底空串，不触发 IntegrityError
	card = _make_card(sid="sec_a", jid="job_a", drop=("jobName", "brandName", "cityName", "jobSalary"))
	platform.job_favorites.return_value = {
		"code": 0,
		"zpData": {"cardList": [card], "hasMore": False},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "favorites", "sync"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["imported_count"] == 1
	items = _shortlist_items(tmp_path)
	assert items[0]["title"] == ""
	assert items[0]["salary"] == ""


@patch("boss_agent_cli.commands.favorites.get_platform_instance")
@patch("boss_agent_cli.commands.favorites.AuthManager")
def test_favorites_sync_duplicate_in_batch(mock_auth_cls, mock_platform_cls, tmp_path):
	platform = _ctx_mock(mock_platform_cls)
	# cardList 内两张相同 (sid, jid)：INSERT OR IGNORE 防第二条 PK 冲突
	platform.job_favorites.return_value = {
		"code": 0,
		"zpData": {"cardList": [_make_card(sid="sec_a", jid="job_a"), _make_card(sid="sec_a", jid="job_a")], "hasMore": False},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "favorites", "sync"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["imported_count"] == 1
	assert parsed["data"]["existing_count"] == 1
	assert len(_shortlist_items(tmp_path)) == 1


@patch("boss_agent_cli.commands.favorites.get_platform_instance")
@patch("boss_agent_cli.commands.favorites.AuthManager")
def test_favorites_sync_sid_changed_still_skipped(mock_auth_cls, mock_platform_cls, tmp_path):
	"""C3 实测：securityId 每次请求变化，按 job_id 去重——同职位 sid 变也不重复导入。"""
	runner = CliRunner()
	platform = _ctx_mock(mock_platform_cls)
	# 第一次 sync：sid=v1
	platform.job_favorites.return_value = {
		"code": 0,
		"zpData": {"cardList": [_make_card(sid="sec_v1", jid="job_a")], "hasMore": False},
	}
	r1 = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "favorites", "sync"])
	assert r1.exit_code == 0
	assert json.loads(r1.output)["data"]["imported_count"] == 1

	# 第二次 sync：同职位 sid 变成 v2（模拟 securityId 跨请求变化）
	platform.job_favorites.return_value = {
		"code": 0,
		"zpData": {"cardList": [_make_card(sid="sec_v2", jid="job_a")], "hasMore": False},
	}
	r2 = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "favorites", "sync"])
	assert r2.exit_code == 0
	parsed = json.loads(r2.output)
	# job_a 已存在 → 跳过，不重复导入（按 job_id 去重，而非不稳的 sid）
	assert parsed["data"]["imported_count"] == 0
	assert parsed["data"]["existing_count"] == 1
	items = _shortlist_items(tmp_path)
	assert len(items) == 1
	assert items[0]["security_id"] == "sec_v2"


@patch("boss_agent_cli.commands.favorites.get_platform_instance")
@patch("boss_agent_cli.commands.favorites.AuthManager")
def test_favorites_sync_null_identifiers_are_skipped(mock_auth_cls, mock_platform_cls, tmp_path):
	platform = _ctx_mock(mock_platform_cls)
	platform.job_favorites.return_value = {
		"code": 0,
		"zpData": {
			"cardList": [
				_make_card(sid=None, jid="job_a"),
				_make_card(sid="sec_b", jid=None),
			],
			"hasMore": False,
		},
	}
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "favorites", "sync"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"] == {"imported_count": 0, "existing_count": 0, "skipped_count": 2}
	assert _shortlist_items(tmp_path) == []


def test_collect_favorites_rejects_incomplete_page_budget():
	from unittest.mock import MagicMock

	from boss_agent_cli.commands.favorites import FavoritesPageLimitExceeded, collect_favorites_items

	platform = MagicMock()
	platform.is_success.return_value = True
	platform.unwrap_data.side_effect = lambda response: response["zpData"]
	platform.job_favorites.side_effect = [
		{"code": 0, "zpData": {"cardList": [_make_card(jid="job_1")], "hasMore": True}},
		{"code": 0, "zpData": {"cardList": [_make_card(jid="job_2")], "hasMore": True}},
	]
	with pytest.raises(FavoritesPageLimitExceeded):
		collect_favorites_items(platform, max_pages=2)


@patch("boss_agent_cli.commands.favorites.collect_favorites_items")
@patch("boss_agent_cli.commands.favorites.get_platform_instance")
@patch("boss_agent_cli.commands.favorites.AuthManager")
def test_favorites_sync_page_limit_returns_error(mock_auth_cls, mock_platform_cls, mock_collect, tmp_path):
	from boss_agent_cli.commands.favorites import FavoritesPageLimitExceeded

	_ctx_mock(mock_platform_cls)
	mock_collect.side_effect = FavoritesPageLimitExceeded("超过安全分页上限")
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "favorites", "sync"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "RESULT_LIMIT_REACHED"
	assert parsed["error"]["recoverable"] is True
	assert _shortlist_items(tmp_path) == []


def test_favorites_public_commands_do_not_expose_tag_override():
	runner = CliRunner()
	for command in (["favorites", "list", "--help"], ["favorites", "sync", "--help"]):
		result = runner.invoke(cli, command)
		assert result.exit_code == 0
		assert "--tag" not in result.output


@patch("boss_agent_cli.commands.favorites.get_platform_instance")
@patch("boss_agent_cli.commands.favorites.AuthManager")
def test_favorites_list_not_supported(mock_auth_cls, mock_platform_cls):
	platform = _ctx_mock(mock_platform_cls)
	platform.job_favorites.side_effect = NotImplementedError("job_favorites not supported")
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "favorites", "list"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "NOT_SUPPORTED"


def test_favorites_schema_exposes_subcommands():
	from boss_agent_cli.commands.schema import SCHEMA_DATA
	spec = SCHEMA_DATA["commands"]["favorites"]
	assert set(spec["subcommands"].keys()) == {"list", "sync"}
	assert set(spec["options"].keys()) == {"list", "sync"}
	assert "39 个顶层命令" in SCHEMA_DATA["description"]


# ── 边界与合规（维护者 #354 要求覆盖）──────────────────────────


@patch("boss_agent_cli.commands.favorites.get_platform_instance")
@patch("boss_agent_cli.commands.favorites.AuthManager")
def test_favorites_list_empty(mock_auth_cls, mock_platform_cls):
	"""空收藏列表：cardList=[] → ok=True，data=[]，total=0。"""
	platform = _ctx_mock(mock_platform_cls)
	platform.job_favorites.return_value = {"code": 0, "zpData": {"cardList": [], "hasMore": False}}
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "favorites", "list"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"] == []
	assert parsed["pagination"]["total"] == 0


@patch("boss_agent_cli.commands.favorites.get_platform_instance")
@patch("boss_agent_cli.commands.favorites.AuthManager")
def test_favorites_list_auth_expired(mock_auth_cls, mock_platform_cls):
	"""登录态失效（AuthRequired）→ AUTH_REQUIRED 信封，exit 1，recoverable=True。"""
	from boss_agent_cli.auth.manager import AuthRequired
	platform = _ctx_mock(mock_platform_cls)
	platform.job_favorites.side_effect = AuthRequired("登录状态已失效")
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "favorites", "list"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "AUTH_REQUIRED"
	assert parsed["error"]["recoverable"] is True


def test_favorites_not_blocked_in_assisted_mode():
	"""favorites 不登记 compliance，assisted 默认模式放行（不在 low_risk blocked 清单）。"""
	from boss_agent_cli.compliance import low_risk_blocked_commands
	blocked = low_risk_blocked_commands()
	assert not any(cmd.startswith("favorites") for cmd in blocked)


def test_favorites_fixture_drives_card_mapping():
	"""脱敏 fixture（真实字段名，C1 实测）验证 _card_to_shortlist_item 映射，锁字段名。"""
	import json
	from pathlib import Path
	from boss_agent_cli.commands.favorites import _card_to_shortlist_item
	fixture_path = Path(__file__).parent / "fixtures" / "favorites_card.json"
	card = json.loads(fixture_path.read_text(encoding="utf-8"))["zpData"]["cardList"][0]
	item = _card_to_shortlist_item(card)
	assert item["security_id"] == "sec_sample_REDACTED"
	assert item["job_id"] == "job_sample_REDACTED"
	assert item["title"] == "示例职位标题"
	assert item["company"] == "示例公司"
	assert item["salary"] == "12-24K"
	assert item["city"] == "北京"
	assert item["source"] == "favorites"

	card["jobLabels"] = "not-a-list"
	assert _card_to_shortlist_item(card)["tags"] == []
