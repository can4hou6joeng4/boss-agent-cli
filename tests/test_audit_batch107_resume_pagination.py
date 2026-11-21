"""批次 107：空 zpData 回落与分页上限提示（Issue #2433、#2434）。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.services.pagination import MAX_TRUSTED_PAGE, list_pagination, page_can_advance
from boss_agent_cli.services.resume_parser import parse_resume


# ── Issue #2433：空 zpData 不能遮蔽旧格式 data ──────────────────────


def test_empty_zpdata_falls_back_to_legacy_data() -> None:
	parsed = parse_resume(
		{
			"zpData": {},
			"data": {"geekDetailInfo": {"geekBaseInfo": {"name": "real", "age": 30}}},
		}
	)

	assert parsed["basic"]["name"] == "real"


def test_non_empty_zpdata_still_wins() -> None:
	parsed = parse_resume(
		{
			"zpData": {"geekDetailInfo": {"geekBaseInfo": {"name": "from-zpdata"}}},
			"data": {"geekDetailInfo": {"geekBaseInfo": {"name": "from-data"}}},
		}
	)

	assert parsed["basic"]["name"] == "from-zpdata"


def test_empty_zpdata_without_legacy_data_yields_empty_resume() -> None:
	parsed = parse_resume({"zpData": {}})

	assert parsed["basic"]["name"] == ""


# ── Issue #2434：达到可信页码上限后下一页提示不再前进 ────────────────


def test_page_can_advance_below_limit() -> None:
	assert page_can_advance(list_pagination({"page": 2, "hasMore": True}, page=2)) is True


def test_page_cannot_advance_at_limit() -> None:
	pagination = list_pagination({"page": MAX_TRUSTED_PAGE, "hasMore": True}, page=MAX_TRUSTED_PAGE)

	assert pagination["page"] == MAX_TRUSTED_PAGE
	assert pagination["has_more"] is True
	assert page_can_advance(pagination) is False


def test_page_cannot_advance_when_request_page_is_clamped() -> None:
	pagination = list_pagination({"hasMore": True}, page=MAX_TRUSTED_PAGE + 1)

	assert pagination["page"] == MAX_TRUSTED_PAGE
	assert page_can_advance(pagination) is False


def test_page_can_advance_tolerates_malformed_page() -> None:
	assert page_can_advance({}) is False
	assert page_can_advance({"page": "2"}) is False
	assert page_can_advance({"page": True}) is False


# ── 命令层：上限页不再生成不可执行的下一页命令 ────────────────────


def _run_chatmsg(tmp_path: Path, page: int) -> dict:
	payload = {"code": 0, "zpData": {"page": page, "hasMore": True, "messages": []}}
	with patch("boss_agent_cli.commands.chatmsg.get_platform_instance") as platform_cls, patch(
		"boss_agent_cli.commands.chatmsg.AuthManager"
	):
		client = platform_cls.return_value
		client.__enter__ = lambda self: self
		client.__exit__ = lambda self, *args: None
		client.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
		client.unwrap_data.side_effect = lambda response: response.get("zpData")
		client.friend_list.return_value = {
			"zpData": {"result": [{"uid": 10001, "securityId": "sid_current", "name": "甲"}]}
		}
		client.chat_history.return_value = payload
		result = CliRunner().invoke(
			cli,
			["--data-dir", str(tmp_path), "--json", "chatmsg", "10001", "--page", str(page)],
		)
	assert result.exit_code == 0, result.output
	return json.loads(result.output)


def test_chatmsg_at_page_limit_omits_next_page_hint(tmp_path: Path) -> None:
	parsed = _run_chatmsg(tmp_path, MAX_TRUSTED_PAGE)

	assert parsed["pagination"]["page"] == MAX_TRUSTED_PAGE
	assert parsed["pagination"]["has_more"] is True
	assert not [action for action in parsed["hints"]["next_actions"] if "--page" in action]


def test_chatmsg_below_page_limit_still_hints_next_page(tmp_path: Path) -> None:
	parsed = _run_chatmsg(tmp_path, 2)

	assert [action for action in parsed["hints"]["next_actions"] if "--page 3" in action]
