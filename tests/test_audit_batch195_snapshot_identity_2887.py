"""Issue #2887：当前记录缺 uid 时不能拿轮换 security_id 做跨天身份。"""

import datetime
import json
import os

from boss_agent_cli.services.chat_snapshot import save_snapshot_and_diff


class _Logger:
	def warning(self, message):
		pass


def _write_previous(snapshot_dir, items):
	os.makedirs(snapshot_dir, exist_ok=True)
	day = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
	with open(os.path.join(snapshot_dir, f"{day}.json"), "w", encoding="utf-8") as handle:
		json.dump(items, handle)
	with open(os.path.join(snapshot_dir, f"{day}.meta"), "w", encoding="utf-8") as handle:
		json.dump({"complete": True}, handle)


def test_missing_uid_does_not_treat_rotating_security_id_as_stable(tmp_path):
	snapshot_dir = str(tmp_path / "snapshots")
	_write_previous(snapshot_dir, [{
		"uid": "", "security_id": "old-token", "unread": 0, "name": "张三",
	}])
	today = [{
		"uid": "", "security_id": "new-token", "unread": 3, "name": "张三",
	}]

	result = save_snapshot_and_diff(snapshot_dir, today, _Logger(), complete=True)

	assert result["added"] == []
	assert result["removed"] == []
	assert result["new_unread"] == []
	assert result["complete"] is False


def test_stable_uid_still_supports_cross_day_diff(tmp_path):
	snapshot_dir = str(tmp_path / "snapshots")
	_write_previous(snapshot_dir, [{
		"uid": "u1", "security_id": "old-token", "unread": 0, "name": "张三",
	}])
	today = [{
		"uid": "u1", "security_id": "new-token", "unread": 3, "name": "张三",
	}]

	result = save_snapshot_and_diff(snapshot_dir, today, _Logger(), complete=True)

	assert result["added"] == []
	assert result["removed"] == []
	assert len(result["new_unread"]) == 1
	assert result["complete"] is True
