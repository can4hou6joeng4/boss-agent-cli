"""沟通列表分页采信平台纠正页码（Issue #3115）。"""

import json
from typing import Any
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.services.pagination import MAX_TRUSTED_PAGE

FRIEND = {
	"uid": 10001,
	"name": "联系人甲",
	"securityId": "sid_current",
	"encryptJobId": "job",
	"lastTS": 1700000000000,
}


def _friend_list(**extra: Any) -> dict[str, Any]:
	return {"code": 0, "zpData": {"result": [FRIEND], "friendList": [FRIEND], **extra}}


def _invoke(tmp_path: Any, *, page: int = 2, payload: dict[str, Any]) -> tuple[dict[str, Any], Any]:
	with patch("boss_agent_cli.commands.chat.get_platform_instance") as factory, \
			patch("boss_agent_cli.commands.chat.AuthManager") as auth:
		client = factory.return_value
		client.__enter__ = lambda self: self
		client.__exit__ = lambda self, *args: None
		client.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
		client.unwrap_data.side_effect = lambda response: response.get("zpData", response.get("data"))
		client.parse_error.return_value = ("NETWORK_ERROR", "fixture")
		client.friend_list.return_value = payload
		auth.return_value.get_token.return_value = None
		result = CliRunner().invoke(
			cli, ["--data-dir", str(tmp_path), "--json", "chat", "--page", str(page)]
		)
	assert result.exit_code == 0, result.output
	return json.loads(result.output), client


def _next_pages(envelope: dict[str, Any]) -> list[str]:
	return [action for action in envelope["hints"]["next_actions"] if "chat --page" in action]


def test_platform_corrected_page_drives_envelope_and_next_action(tmp_path: Any) -> None:
	envelope, client = _invoke(tmp_path, payload=_friend_list(page=4, hasMore=True))

	assert envelope["pagination"] == {
		"page": 4,
		"has_more": True,
		"has_more_source": "platform_hasMore",
		"total": 1,
		"total_kind": "returned_page_count",
	}
	assert _next_pages(envelope)[0].endswith("chat --page 5")
	assert client.friend_list.call_args.kwargs == {"page": 2}


def test_invalid_platform_page_falls_back_to_request(tmp_path: Any) -> None:
	envelope, _ = _invoke(tmp_path, payload=_friend_list(page="4", hasMore=True))

	assert envelope["pagination"]["page"] == 2
	assert envelope["pagination"]["has_more"] is True
	assert _next_pages(envelope)[0].endswith("chat --page 3")


def test_has_more_type_drift_keeps_unknown_contract(tmp_path: Any) -> None:
	envelope, _ = _invoke(tmp_path, payload=_friend_list(page=4, hasMore="true"))

	assert envelope["pagination"]["page"] == 4
	assert envelope["pagination"]["has_more"] is None
	assert envelope["pagination"]["has_more_source"] == "unknown"
	assert _next_pages(envelope) == []


def test_last_trusted_page_stops_next_action(tmp_path: Any) -> None:
	envelope, _ = _invoke(tmp_path, payload=_friend_list(page=MAX_TRUSTED_PAGE, hasMore=True))

	assert envelope["pagination"]["page"] == MAX_TRUSTED_PAGE
	assert _next_pages(envelope) == []
