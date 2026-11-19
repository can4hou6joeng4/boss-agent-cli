"""配置与 me 分页的回归（Issue #1822 #1823 #1824）。"""

from __future__ import annotations

import json
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.config import DEFAULTS
from boss_agent_cli.main import cli
from boss_agent_cli.services.pagination import MAX_TRUSTED_PAGE, list_pagination


def _run(tmp_path, *args):
	out = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])
	return out.exit_code, json.loads(out.output)


# ── #1822：request_budget_dir 必须是目录 ──────────────────────


def test_config_set_rejects_existing_file_as_budget_dir(tmp_path):
	not_a_dir = tmp_path / "notadir"
	not_a_dir.write_text("x", encoding="utf-8")
	code, payload = _run(tmp_path, "config", "set", "request_budget_dir", str(not_a_dir))
	assert code == 1
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "不是目录" in payload["error"]["message"]
	assert not (tmp_path / "config.json").exists()
	assert _run(tmp_path, "cities")[0] == 0


def test_config_set_accepts_missing_or_directory_budget_dir(tmp_path):
	code, _ = _run(tmp_path, "config", "set", "request_budget_dir", str(tmp_path / "group"))
	assert code == 0
	(tmp_path / "group").mkdir()
	code, payload = _run(tmp_path, "config", "set", "request_budget_dir", str(tmp_path / "group"))
	assert code == 0, payload


def test_startup_error_points_at_config_reset(tmp_path):
	not_a_dir = tmp_path / "notadir"
	not_a_dir.write_text("x", encoding="utf-8")
	(tmp_path / "config.json").write_text(json.dumps({"request_budget_dir": str(not_a_dir)}), encoding="utf-8")
	code, payload = _run(tmp_path, "cities")
	assert code == 1
	assert "config reset request_budget_dir" in payload["error"]["recovery_action"]


# ── #1823：reset operating_mode 清除 legacy 键 ───────────────


def test_config_reset_operating_mode_clears_legacy_key(tmp_path):
	(tmp_path / "config.json").write_text(json.dumps({"low_risk_mode": False}), encoding="utf-8")
	code, payload = _run(tmp_path, "config", "reset", "operating_mode")
	assert code == 0, payload
	assert payload["data"]["value"] == DEFAULTS["operating_mode"]
	stored = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
	assert "low_risk_mode" not in stored
	code, payload = _run(tmp_path, "config", "get", "operating_mode")
	# 测试夹具把默认模式设为 research；关键是 legacy 键不再把它锁成 research 之外的值。
	assert payload["data"]["value"] == DEFAULTS["operating_mode"]


def test_config_list_marks_legacy_derived_mode_as_custom(tmp_path):
	(tmp_path / "config.json").write_text(json.dumps({"low_risk_mode": False}), encoding="utf-8")
	code, payload = _run(tmp_path, "config", "list")
	assert code == 0
	item = next(i for i in payload["data"]["items"] if i["key"] == "operating_mode")
	assert item["value"] == "research" and item["source"] == "用户配置"


# ── #1824：超长 page 不进下一页提示 ──────────────────────────


def test_pagination_ignores_untrustworthy_page():
	pagination = list_pagination({"page": int("9" * 4300), "hasMore": True}, page=2)
	assert pagination["page"] == 2
	assert pagination["has_more"] is True
	assert list_pagination({"page": MAX_TRUSTED_PAGE + 1, "hasMore": True}, page=1)["page"] == 1
	assert list_pagination({"page": MAX_TRUSTED_PAGE, "hasMore": True}, page=1)["page"] == MAX_TRUSTED_PAGE


def test_next_page_hint_uses_requested_page_for_huge_platform_page(tmp_path):
	page = int("9" * 4300)
	with patch("boss_agent_cli.commands.me.AuthManager"), patch(
		"boss_agent_cli.commands.me.get_platform_instance"
	) as mock_platform:
		client = mock_platform.return_value
		client.__enter__ = lambda self: self
		client.__exit__ = lambda self, *a: None
		client.unwrap_data.side_effect = lambda r: r.get("zpData") if "zpData" in r else r.get("data")
		client.is_success.side_effect = lambda r: r.get("code", 0) in (0, 200)
		client.user_info.return_value = {"zpData": {"name": "n"}}
		client.deliver_list.return_value = {"zpData": {"page": page, "hasMore": True, "list": []}}
		out = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "me", "--section", "deliver"])
	assert out.exit_code == 0, out.output
	hints = json.loads(out.output)["hints"]["next_actions"]
	assert any("--deliver-page 2" in hint for hint in hints)
