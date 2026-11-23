"""已保存搜索参数（watch / preset）的读取侧校验。

存量记录可能由旧版本、wizard 或手工编辑写入：

- 关键词缺失 / 空白会让 run 以 None / 空串发起空泛搜索并返回 ``ok:true``
  （Issue #1798）；
- 类型漂移与未知城市 / 标签取值会让 ``ensure_supported_city`` / ``.split()``
  抛 ``TypeError`` / ``AttributeError``，被兜成可重试的 ``NETWORK_ERROR``
  （Issue #1338）。

CLI ``watch run`` 与向导共用这一份判定，避免两边口径漂移（Issue #2511）。
"""

from __future__ import annotations

from typing import Any, Mapping

from boss_agent_cli.search_filters import (
	ensure_supported_city,
	resolve_active_level,
	resolve_search_code_params,
)
from boss_agent_cli.services.text_files import is_utf8_encodable

#: 存储格式为「字符串或 null」的参数键：``build_search_params`` 的 10 键加 active。
SEARCH_PARAM_KEYS = (
	"query",
	"city",
	"salary",
	"experience",
	"education",
	"industry",
	"scale",
	"stage",
	"job_type",
	"welfare",
	"active",
)


def validate_stored_search_params(params: Mapping[str, Any]) -> None:
	"""校验一份已保存的搜索参数；不可运行时抛 ``ValueError``（消息可直接展示）。

	调用方负责把 ``ValueError`` 映射成自己的错误码与恢复指引：CLI
	``watch run`` 报 ``SAVED_SEARCH_INVALID``，向导的 ``candidate_watch`` 动作
	同样报 ``SAVED_SEARCH_INVALID`` 并附删除重建命令。
	"""
	query_value = params.get("query")
	if not isinstance(query_value, str) or not query_value.strip():
		raise ValueError("关键词不能为空（缺失或为空值）")
	for key in SEARCH_PARAM_KEYS:
		value = params.get(key)
		if value is not None and not isinstance(value, str):
			# 存量参数可能被手工改成数组 / 数字：先在类型上失败，避免
			# `ensure_supported_city` / `.split()` 抛 TypeError / AttributeError。
			raise ValueError(f"参数 {key} 必须是字符串（收到 {type(value).__name__}）")
		if isinstance(value, str) and not is_utf8_encodable(value):
			# 关键词最终进入 httpx 的 URL 参数：孤立代理项会在那里抛
			# UnicodeEncodeError，被兜成带磁盘指引的 LOCAL_IO_ERROR，且
			# `watch run --all` 的一条坏记录会中止整批（Issue #2528）。
			raise ValueError(f"参数 {key} 含有无法按 UTF-8 编码的字符（如孤立代理项）")
	ensure_supported_city(params.get("city"))
	# active 与其他筛选键同口径：未知取值必须落成 SAVED_SEARCH_INVALID，
	# 不能留到 pipeline 里抛裸 ValueError 被兜成可重试的 NETWORK_ERROR（Issue #1847）。
	resolve_active_level(params.get("active"))
	resolve_search_code_params(
		salary=params.get("salary"),
		experience=params.get("experience"),
		education=params.get("education"),
		industry=params.get("industry"),
		scale=params.get("scale"),
		stage=params.get("stage"),
		job_type=params.get("job_type"),
	)
