"""Chat snapshot — JSON 快照保存与 diff 对比。"""

import datetime
import json
import os
from typing import Any

from boss_agent_cli.services.chat_utils import contact_identity
from boss_agent_cli.output import Logger


def save_snapshot_and_diff(
	snapshot_dir: str, friends: list[dict[str, Any]], logger: Logger
) -> dict[str, Any]:
	"""保存当日 JSON 快照（uid 优先，旧数据回退 security_id）并与上次对比。"""
	os.makedirs(snapshot_dir, exist_ok=True)
	today = datetime.date.today().isoformat()
	snapshot_path = os.path.join(snapshot_dir, f"{today}.json")

	# 合并已有的当日快照（解决分页覆盖问题）
	existing = {}
	if os.path.exists(snapshot_path):
		try:
			with open(snapshot_path, encoding="utf-8") as f:
				prev_today = json.load(f)
			if isinstance(prev_today, list):
				for item in prev_today:
					if isinstance(item, dict) and contact_identity(item):
						existing[contact_identity(item)] = item
		except (json.JSONDecodeError, OSError):
			pass

	# 用当前数据更新（新数据优先）
	for item in friends:
		key = contact_identity(item)
		if key:
			existing[key] = item

	merged = list(existing.values())

	# 保存合并后的快照
	with open(snapshot_path, "w", encoding="utf-8") as f:
		json.dump(merged, f, ensure_ascii=False, indent=2)

	# 查找上一次快照
	prev_path = _find_previous_snapshot(snapshot_dir, today)
	if prev_path is None:
		return {"is_first": True, "added": [], "removed": [], "new_unread": []}

	prev_friends = load_snapshot(prev_path, logger)
	if prev_friends is None:
		return {"is_first": True, "added": [], "removed": [], "new_unread": []}

	# 用稳定标识（uid 优先）做 key 对比
	curr_map = {contact_identity(item): item for item in merged if contact_identity(item)}
	prev_map = {contact_identity(item): item for item in prev_friends if contact_identity(item)}
	curr_ids = set(curr_map)
	prev_ids = set(prev_map)

	added = [curr_map[key] for key in (curr_ids - prev_ids)]
	removed = [prev_map[key] for key in (prev_ids - curr_ids)]

	# 新增未读：检测任何未读增量
	new_unread = []
	for key in (curr_ids & prev_ids):
		prev_unread = prev_map[key].get("unread", 0) or 0
		curr_unread = curr_map[key].get("unread", 0) or 0
		if curr_unread > prev_unread:
			new_unread.append(curr_map[key])

	return {
		"is_first": False,
		"prev_date": os.path.basename(prev_path).replace(".json", ""),
		"added": added,
		"removed": removed,
		"new_unread": new_unread,
	}


def load_snapshot(path: str, logger: Logger) -> list[dict[str, Any]] | None:
	"""加载并校验快照文件，返回 None 表示不可用。"""
	try:
		with open(path, encoding="utf-8") as f:
			data = json.load(f)
	except (json.JSONDecodeError, OSError):
		logger.warning(f"无法读取快照: {path}")
		return None

	if not isinstance(data, list):
		logger.warning(f"快照格式异常（非数组）: {path}")
		return None

	# 过滤掉非 dict 项
	return [item for item in data if isinstance(item, dict)]


def _find_previous_snapshot(snapshot_dir: str, today: str) -> str | None:
	"""找到 today 之前最近的一份快照文件路径。"""
	snapshots = []
	for fname in os.listdir(snapshot_dir):
		if fname.endswith(".json") and fname[:10] != today:
			snapshots.append(fname)
	if not snapshots:
		return None
	snapshots.sort(reverse=True)
	return os.path.join(snapshot_dir, snapshots[0])
