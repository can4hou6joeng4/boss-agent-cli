"""chat_snapshot.py 扩展测试 — 覆盖快照保存、增量对比、边界与降级。"""

import datetime
import json
import os
from unittest.mock import MagicMock

from boss_agent_cli.commands.chat_snapshot import (
	save_snapshot_and_diff,
	load_snapshot,
	_find_previous_snapshot,
)


def _make_logger():
	logger = MagicMock()
	return logger


def _make_items(*entries):
	"""快速构造 friends 列表。entries: [(name, sid, unread), ...]"""
	result = []
	for name, sid, unread in entries:
		result.append({
			"name": name,
			"security_id": sid,
			"unread": unread,
			"brand_name": "TestCo",
		})
	return result


# ── 1. 首次快照（无历史文件） ────────────────────────────────────────

def test_first_snapshot_no_history(tmp_path):
	"""无任何历史快照时应返回 is_first=True。"""
	snapshot_dir = str(tmp_path / "snapshots")
	friends = _make_items(("张三", "sid_a", 0), ("李四", "sid_b", 2))
	logger = _make_logger()

	result = save_snapshot_and_diff(snapshot_dir, friends, logger)

	assert result["is_first"] is True
	assert result["added"] == []
	assert result["removed"] == []
	assert result["new_unread"] == []

	# 验证快照文件已写入
	today = datetime.date.today().isoformat()
	snapshot_path = os.path.join(snapshot_dir, f"{today}.json")
	assert os.path.exists(snapshot_path)

	with open(snapshot_path, encoding="utf-8") as f:
		saved = json.load(f)
	assert len(saved) == 2
	sids = {item["security_id"] for item in saved}
	assert sids == {"sid_a", "sid_b"}


def test_first_snapshot_empty_dir(tmp_path):
	"""快照目录存在但无文件时应返回 is_first=True。"""
	snapshot_dir = str(tmp_path / "snapshots")
	os.makedirs(snapshot_dir)
	friends = _make_items(("HR", "sid_x", 0))
	result = save_snapshot_and_diff(snapshot_dir, friends, _make_logger())
	assert result["is_first"] is True


# ── 2. 增量对比 — 检测新增联系人 ─────────────────────────────────────

def test_diff_detects_added_contacts(tmp_path):
	"""新增联系人应出现在 added 列表中。"""
	snapshot_dir = str(tmp_path / "snapshots")
	os.makedirs(snapshot_dir)

	# 写入昨天的快照
	yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
	prev_data = _make_items(("张三", "sid_a", 0))
	with open(os.path.join(snapshot_dir, f"{yesterday}.json"), "w", encoding="utf-8") as f:
		json.dump(prev_data, f)

	# 今天新增了李四
	friends = _make_items(("张三", "sid_a", 0), ("李四", "sid_b", 1))
	result = save_snapshot_and_diff(snapshot_dir, friends, _make_logger())

	assert result["is_first"] is False
	assert result["prev_date"] == yesterday
	assert len(result["added"]) == 1
	assert result["added"][0]["security_id"] == "sid_b"
	assert len(result["removed"]) == 0


# ── 3. 增量对比 — 检测消失联系人 ─────────────────────────────────────

def test_diff_detects_removed_contacts(tmp_path):
	"""消失的联系人应出现在 removed 列表中。"""
	snapshot_dir = str(tmp_path / "snapshots")
	os.makedirs(snapshot_dir)

	yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
	prev_data = _make_items(("张三", "sid_a", 0), ("李四", "sid_b", 0))
	with open(os.path.join(snapshot_dir, f"{yesterday}.json"), "w", encoding="utf-8") as f:
		json.dump(prev_data, f)

	# 今天只剩张三
	friends = _make_items(("张三", "sid_a", 0))
	result = save_snapshot_and_diff(snapshot_dir, friends, _make_logger())

	assert result["is_first"] is False
	assert len(result["removed"]) == 1
	assert result["removed"][0]["security_id"] == "sid_b"
	assert len(result["added"]) == 0


# ── 4. 增量对比 — 检测新消息（未读增量） ──────────────────────────────

def test_diff_detects_new_unread(tmp_path):
	"""联系人 unread 增加时应出现在 new_unread 列表中。"""
	snapshot_dir = str(tmp_path / "snapshots")
	os.makedirs(snapshot_dir)

	yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
	prev_data = _make_items(("张三", "sid_a", 0), ("李四", "sid_b", 2))
	with open(os.path.join(snapshot_dir, f"{yesterday}.json"), "w", encoding="utf-8") as f:
		json.dump(prev_data, f)

	# 今天张三有 3 条新未读，李四未读数没变
	friends = _make_items(("张三", "sid_a", 3), ("李四", "sid_b", 2))
	result = save_snapshot_and_diff(snapshot_dir, friends, _make_logger())

	assert len(result["new_unread"]) == 1
	assert result["new_unread"][0]["security_id"] == "sid_a"


def test_diff_no_new_unread_when_decreased(tmp_path):
	"""联系人 unread 减少（已读）不应出现在 new_unread。"""
	snapshot_dir = str(tmp_path / "snapshots")
	os.makedirs(snapshot_dir)

	yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
	prev_data = _make_items(("张三", "sid_a", 5))
	with open(os.path.join(snapshot_dir, f"{yesterday}.json"), "w", encoding="utf-8") as f:
		json.dump(prev_data, f)

	friends = _make_items(("张三", "sid_a", 2))
	result = save_snapshot_and_diff(snapshot_dir, friends, _make_logger())

	assert result["new_unread"] == []


# ── 5. 损坏快照文件降级处理 ──────────────────────────────────────────

def test_corrupted_previous_snapshot_treated_as_first(tmp_path):
	"""上一份快照损坏（非法 JSON）时应降级为首次快照。"""
	snapshot_dir = str(tmp_path / "snapshots")
	os.makedirs(snapshot_dir)

	yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
	with open(os.path.join(snapshot_dir, f"{yesterday}.json"), "w") as f:
		f.write("{invalid json!!!")

	friends = _make_items(("张三", "sid_a", 0))
	logger = _make_logger()
	result = save_snapshot_and_diff(snapshot_dir, friends, logger)

	assert result["is_first"] is True
	logger.warning.assert_called()


def test_non_array_previous_snapshot_treated_as_first(tmp_path):
	"""上一份快照是 JSON 但非数组时应降级为首次快照。"""
	snapshot_dir = str(tmp_path / "snapshots")
	os.makedirs(snapshot_dir)

	yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
	with open(os.path.join(snapshot_dir, f"{yesterday}.json"), "w") as f:
		json.dump({"not": "an array"}, f)

	friends = _make_items(("张三", "sid_a", 0))
	logger = _make_logger()
	result = save_snapshot_and_diff(snapshot_dir, friends, logger)

	assert result["is_first"] is True
	logger.warning.assert_called()


def test_corrupted_today_snapshot_still_works(tmp_path):
	"""当日已有损坏快照时，应能正常覆盖写入。"""
	snapshot_dir = str(tmp_path / "snapshots")
	os.makedirs(snapshot_dir)

	today = datetime.date.today().isoformat()
	with open(os.path.join(snapshot_dir, f"{today}.json"), "w") as f:
		f.write("not valid json")

	friends = _make_items(("张三", "sid_a", 0))
	save_snapshot_and_diff(snapshot_dir, friends, _make_logger())

	# 不会崩溃，且写入了新的快照
	with open(os.path.join(snapshot_dir, f"{today}.json"), encoding="utf-8") as f:
		saved = json.load(f)
	assert len(saved) == 1
	assert saved[0]["security_id"] == "sid_a"


# ── 6. 空数据/None 数据兜底 ──────────────────────────────────────────

def test_empty_friends_list(tmp_path):
	"""传入空列表时应正常保存空快照。"""
	snapshot_dir = str(tmp_path / "snapshots")
	result = save_snapshot_and_diff(snapshot_dir, [], _make_logger())
	assert result["is_first"] is True

	today = datetime.date.today().isoformat()
	with open(os.path.join(snapshot_dir, f"{today}.json"), encoding="utf-8") as f:
		saved = json.load(f)
	assert saved == []


def test_friends_with_no_security_id(tmp_path):
	"""security_id 缺失的项不应进入去重 map。"""
	snapshot_dir = str(tmp_path / "snapshots")
	friends = [
		{"name": "无ID", "unread": 0},  # 无 security_id
		{"name": "有ID", "security_id": "sid_x", "unread": 1},
	]
	save_snapshot_and_diff(snapshot_dir, friends, _make_logger())

	today = datetime.date.today().isoformat()
	with open(os.path.join(snapshot_dir, f"{today}.json"), encoding="utf-8") as f:
		saved = json.load(f)
	# 无 security_id 的项不参与 merge（dict key 为 None），但仍保留在 merged
	# 具体行为取决于 existing dict 的处理，有 ID 的那条一定在
	assert any(item.get("security_id") == "sid_x" for item in saved)


def test_unread_none_treated_as_zero(tmp_path):
	"""unread 为 None 时应兜底为 0，不抛异常。"""
	snapshot_dir = str(tmp_path / "snapshots")
	os.makedirs(snapshot_dir)

	yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
	prev_data = [{"name": "张三", "security_id": "sid_a", "unread": None}]
	with open(os.path.join(snapshot_dir, f"{yesterday}.json"), "w", encoding="utf-8") as f:
		json.dump(prev_data, f)

	friends = [{"name": "张三", "security_id": "sid_a", "unread": 3}]
	result = save_snapshot_and_diff(snapshot_dir, friends, _make_logger())

	# unread: None -> 3 应检测为新消息
	assert len(result["new_unread"]) == 1


# ── 7. 分页合并（同天多次调用不覆盖） ────────────────────────────────

def test_same_day_merge_not_overwrite(tmp_path):
	"""同天第二次调用应合并而非覆盖已有快照。"""
	snapshot_dir = str(tmp_path / "snapshots")
	os.makedirs(snapshot_dir)

	today = datetime.date.today().isoformat()
	first_page = _make_items(("张三", "sid_a", 0))
	with open(os.path.join(snapshot_dir, f"{today}.json"), "w", encoding="utf-8") as f:
		json.dump(first_page, f)

	# 第二页数据
	second_page = _make_items(("李四", "sid_b", 1))
	save_snapshot_and_diff(snapshot_dir, second_page, _make_logger())

	with open(os.path.join(snapshot_dir, f"{today}.json"), encoding="utf-8") as f:
		merged = json.load(f)
	sids = {item["security_id"] for item in merged}
	assert "sid_a" in sids
	assert "sid_b" in sids


def test_same_day_update_existing_entry(tmp_path):
	"""同天重复 security_id 应以最新数据覆盖旧数据。"""
	snapshot_dir = str(tmp_path / "snapshots")
	os.makedirs(snapshot_dir)

	today = datetime.date.today().isoformat()
	old_data = [{"name": "张三", "security_id": "sid_a", "unread": 0}]
	with open(os.path.join(snapshot_dir, f"{today}.json"), "w", encoding="utf-8") as f:
		json.dump(old_data, f)

	# 同一个 sid，unread 更新
	new_data = [{"name": "张三", "security_id": "sid_a", "unread": 5}]
	save_snapshot_and_diff(snapshot_dir, new_data, _make_logger())

	with open(os.path.join(snapshot_dir, f"{today}.json"), encoding="utf-8") as f:
		merged = json.load(f)
	assert len(merged) == 1
	assert merged[0]["unread"] == 5


# ── 8. load_snapshot 单元测试 ────────────────────────────────────────

def test_load_snapshot_valid(tmp_path):
	"""正常 JSON 数组文件应返回列表。"""
	path = str(tmp_path / "snap.json")
	data = [{"security_id": "a"}, {"security_id": "b"}]
	with open(path, "w") as f:
		json.dump(data, f)
	result = load_snapshot(path, _make_logger())
	assert result == data


def test_load_snapshot_invalid_json(tmp_path):
	"""非法 JSON 应返回 None。"""
	path = str(tmp_path / "bad.json")
	with open(path, "w") as f:
		f.write("not json")
	logger = _make_logger()
	result = load_snapshot(path, logger)
	assert result is None
	logger.warning.assert_called()


def test_load_snapshot_not_array(tmp_path):
	"""JSON 非数组应返回 None。"""
	path = str(tmp_path / "obj.json")
	with open(path, "w") as f:
		json.dump({"key": "val"}, f)
	logger = _make_logger()
	result = load_snapshot(path, logger)
	assert result is None
	logger.warning.assert_called()


def test_load_snapshot_filters_non_dict_items(tmp_path):
	"""数组中的非 dict 项应被过滤掉。"""
	path = str(tmp_path / "mixed.json")
	data = [{"security_id": "ok"}, "string_item", 123, None, {"security_id": "ok2"}]
	with open(path, "w") as f:
		json.dump(data, f)
	result = load_snapshot(path, _make_logger())
	assert len(result) == 2
	assert result[0]["security_id"] == "ok"
	assert result[1]["security_id"] == "ok2"


# ── 9. _find_previous_snapshot 单元测试 ──────────────────────────────

def test_find_previous_snapshot_picks_latest(tmp_path):
	"""应选取 today 之前最近的一份快照。"""
	snapshot_dir = str(tmp_path)
	# 创建多个历史文件
	for fname in ["2026-04-10.json", "2026-04-11.json", "2026-04-12.json"]:
		with open(os.path.join(snapshot_dir, fname), "w") as f:
			json.dump([], f)

	result = _find_previous_snapshot(snapshot_dir, "2026-04-13")
	assert result is not None
	assert result.endswith("2026-04-12.json")


def test_find_previous_snapshot_excludes_today(tmp_path):
	"""不应选取当天的快照。"""
	snapshot_dir = str(tmp_path)
	with open(os.path.join(snapshot_dir, "2026-04-13.json"), "w") as f:
		json.dump([], f)

	result = _find_previous_snapshot(snapshot_dir, "2026-04-13")
	assert result is None


def test_find_previous_snapshot_empty_dir(tmp_path):
	"""空目录应返回 None。"""
	result = _find_previous_snapshot(str(tmp_path), "2026-04-13")
	assert result is None


def test_find_previous_snapshot_ignores_non_json(tmp_path):
	"""非 .json 文件不应被选取。"""
	snapshot_dir = str(tmp_path)
	with open(os.path.join(snapshot_dir, "2026-04-12.txt"), "w") as f:
		f.write("not a snapshot")

	result = _find_previous_snapshot(snapshot_dir, "2026-04-13")
	assert result is None
