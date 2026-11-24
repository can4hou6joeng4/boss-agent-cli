"""推荐职位列表的归一化：CLI `recommend` 与向导 recommendations 共用。

平台原始 ``jobList`` 需要先按单次结果去重，再归一成 ``JobItem`` 形状、按当前
账号补上 ``greeted``，最后按需附加匹配分。两处入口各写一份时，去重键、
问候态账号与分数计算会各自漂移：向导此前就完全不读 ``with_score``，传 true
仍返回平台原始列表（Issue #2662）。
"""

from __future__ import annotations

from typing import Any

from boss_agent_cli.api.models import JobItem
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.match_score import score_job_dict
from boss_agent_cli.services.job_identity import deduplicate_job_result


def build_recommend_items(
	raw_items: list[dict[str, Any]],
	*,
	cache: CacheStore,
	account: str,
	with_score: bool = False,
	expect_data: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
	"""把平台推荐列表归一成带问候态（及可选匹配分）的职位字典。

	``with_score`` 为 false 时保持与旧命令一致的字段集合，不读取求职期望；
	为 true 时调用方需先取得 ``expect_data``（`resume_expect` 的解包结果），
	由 :func:`score_job_dict` 附加 ``match_score`` 与匹配原因。
	"""
	items: list[dict[str, Any]] = []
	for raw_item in deduplicate_job_result(raw_items):
		item = JobItem.from_api(raw_item)
		item.greeted = cache.is_greeted(item.security_id, job_id=item.job_id, account=account)
		item_dict = item.to_dict()
		if with_score:
			item_dict = score_job_dict(item_dict, criteria=None, expect_data=expect_data)
		items.append(item_dict)
	return items
