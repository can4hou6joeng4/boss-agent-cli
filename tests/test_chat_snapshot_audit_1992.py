"""chat 快照完整度与 detail 通道归一回归（Issue #1992–#1994）。"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_agent_cli.commands.detail import _detail_via_httpx
from boss_agent_cli.main import cli
from boss_agent_cli.services import chat_snapshot


# ── #1992：元素全坏的页不是「完整空列表」 ──────────────────────────


def _friend(uid: str) -> dict[str, object]:
	return {"uid": uid, "security_id": f"sid-{uid}", "name": f"联系人{uid}", "unread": 0, "msg_status": "已读"}


def _snapshot_path(directory: Path, days_ago: int) -> Path:
	day = datetime.date.today() - datetime.timedelta(days=days_ago)
	return directory / f"{day.isoformat()}.json"


def _meta_path(snapshot: Path) -> Path:
	return Path(str(snapshot)[: -len(".json")] + ".meta")


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData")
	instance.is_success.side_effect = lambda response: response.get("code", 0) == 0
	return instance


def _seed_previous_snapshot(tmp_path: Path) -> None:
	snapshot_dir = tmp_path / "chat-history"
	snapshot_dir.mkdir()
	prev_item = {
		"name": "旧联系人",
		"title": "-",
		"brand_name": "-",
		"initiated_by": "对方主动",
		"last_msg": "旧消息",
		"last_time": "昨天",
		"last_ts": 1700000000000,
		"msg_status": "已读",
		"uid": "u_prev",
		"security_id": "sid_prev",
		"encrypt_job_id": "job_prev",
		"unread": 0,
	}
	prev = _snapshot_path(snapshot_dir, 1)
	prev.write_text(json.dumps([prev_item], ensure_ascii=False), encoding="utf-8")
	_meta_path(prev).write_text(json.dumps({"complete": True}), encoding="utf-8")


@patch("boss_agent_cli.commands.chat.get_platform_instance")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_page_with_only_bad_elements_is_not_a_complete_empty_page(
	mock_auth_cls, mock_platform_cls, tmp_path: Path
) -> None:
	"""坏元素页不能替换当日快照，也不能把旧联系人报成已消失（#1992）。"""
	_seed_previous_snapshot(tmp_path)
	platform = _ctx_mock(mock_platform_cls)
	platform.friend_list.return_value = {"zpData": {"result": [None, 5], "hasMore": False}}

	result = CliRunner().invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "chat", "--export", "md", "-o", str(tmp_path / "out.md")],
	)

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is True
	assert payload["data"]["diff"]["removed"] == []
	today = _snapshot_path(tmp_path / "chat-history", 0)
	assert json.loads(_meta_path(today).read_text(encoding="utf-8")) == {"complete": False}


@patch("boss_agent_cli.commands.chat.get_platform_instance")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_mixed_bad_and_good_elements_preserve_contacts_without_inferring_removal(
	mock_auth_cls, mock_platform_cls, tmp_path: Path
) -> None:
	"""记录可用的联系人，过滤掉的条目使这次采集不完整（#2179）。"""
	_seed_previous_snapshot(tmp_path)
	platform = _ctx_mock(mock_platform_cls)
	platform.friend_list.return_value = {
		"zpData": {"result": [None, {"uid": "u_new", "name": "新联系人"}], "hasMore": False}
	}

	result = CliRunner().invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "chat", "--export", "md", "-o", str(tmp_path / "out.md")],
	)

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["data"]["count"] == 1
	assert payload["data"]["diff"]["complete"] is False
	assert payload["data"]["diff"]["removed"] == []
	today = _snapshot_path(tmp_path / "chat-history", 0)
	assert json.loads(_meta_path(today).read_text(encoding="utf-8")) == {"complete": False}


# ── #1993：元素全非对象的基线快照按不可用处理 ────────────────────────


def test_snapshot_with_only_bad_elements_is_not_an_empty_baseline(tmp_path: Path) -> None:
	prev = _snapshot_path(tmp_path, 1)
	prev.write_text(json.dumps([None, "junk", 7]), encoding="utf-8")

	result = chat_snapshot.save_snapshot_and_diff(str(tmp_path), [_friend("u9")], MagicMock(), complete=True)

	assert result["is_first"] is True
	assert result["added"] == []


def test_truly_empty_snapshot_is_still_a_valid_empty_baseline(tmp_path: Path) -> None:
	prev = _snapshot_path(tmp_path, 1)
	prev.write_text("[]", encoding="utf-8")

	result = chat_snapshot.save_snapshot_and_diff(str(tmp_path), [_friend("u9")], MagicMock(), complete=True)

	assert result["is_first"] is False
	assert [item["uid"] for item in result["added"]] == ["u9"]


# ── #1994：detail httpx 通道与浏览器通道同口径 ───────────────────────


def _platform_with_job_info(job_info: dict) -> SimpleNamespace:
	return SimpleNamespace(
		job_detail=lambda job_id: {"code": 0, "zpData": {"jobInfo": job_info, "bossInfo": {}, "brandComInfo": {}}},
		is_success=lambda raw: raw.get("code", 0) == 0,
		unwrap_data=lambda raw: raw.get("zpData"),
		parse_error=lambda raw: ("UNKNOWN", ""),
	)


def test_detail_httpx_normalizes_skills_and_raw_job_type(tmp_path: Path) -> None:
	platform = _platform_with_job_info(
		{"jobName": "职位", "jobLabels": [None, {"k": "v"}, 3], "jobType": {"drifted": True}}
	)

	result, error = _detail_via_httpx(platform, "sid", "job-1", tmp_path)

	assert error is None
	assert result is not None
	assert result["skills"] == ['{"k": "v"}', "3"], "skills 必须是 list[str]，容器元素转 JSON 文本"
	assert result["raw_job_type"] is None


def test_detail_httpx_non_finite_job_type_is_missing(tmp_path: Path) -> None:
	platform = _platform_with_job_info({"jobName": "职位", "jobType": float("inf")})

	result, error = _detail_via_httpx(platform, "sid", "job-1", tmp_path)

	assert error is None
	assert result is not None
	assert result["raw_job_type"] is None
