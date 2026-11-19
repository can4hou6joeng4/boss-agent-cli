"""把 job_card 响应映射为统一职位字段（detail / show / wizard 共用）。"""

import math
from typing import Any

from boss_agent_cli.api.models import employment_type_from_raw, field_list, field_text, safe_text
from boss_agent_cli.services.json_files import strict_json_text


def _text_item(value: Any) -> str:
	"""列表元素渲染为文本：null / 非有限数 → 空串，容器 → JSON 文本，标量 → str。"""
	if value is None:
		return ""
	if isinstance(value, (dict, list, tuple)):
		# 与 job_export._cell_text 同口径：strict_json_text 递归把嵌套的非有限数
		# 归一为 null 并禁止 NaN / Infinity 字面量（Issue #1977）；平台若返回
		# 超深嵌套 / 超大整数无法序列化，展示字段按缺失处理，不能让整条命令
		# 失败（Issue #2065、#2115）。
		try:
			return strict_json_text(value, indent=None)
		except OSError:
			return ""
	if isinstance(value, float) and not math.isfinite(value):
		return ""
	return safe_text(value)


def text_items(data: Any, key: str) -> list[str]:
	"""列表字段逐个归一为文本并丢弃空元素，不把 repr / None 交给展示层。"""
	return [text for item in field_list(data, key) if (text := _text_item(item))]


def scalar_or_none(value: Any) -> Any:
	"""raw_job_type 输出归一：容器与非有限数按缺失处理，不再落 Python repr。"""
	if value is None or isinstance(value, (dict, list, tuple)):
		return None
	if isinstance(value, float) and not math.isfinite(value):
		return None
	return value


def build_job_from_card(card: dict[str, Any], *, security_id: str, greeted: bool, lid: str = "") -> dict[str, Any]:
	"""把 job_card 响应映射为统一职位字段 dict（show / detail 浏览器兜底通道共用）。"""
	if not isinstance(card, dict):
		# 调用方（show / detail / wizard）都已先走 field_dict；公共边界上非对象
		# 入参按空卡片处理，``card.get`` 的 AttributeError 不能让整条命令落成
		# INTERNAL_ERROR（Issue #2162）。
		card = {}
	raw_job_type = card.get("jobType")
	return {
		"job_id": field_text(card, "encryptJobId"),
		"title": field_text(card, "jobName"),
		"company": field_text(card, "brandName"),
		"salary": field_text(card, "salaryDesc"),
		"city": field_text(card, "cityName"),
		"experience": field_text(card, "experienceName"),
		"education": field_text(card, "degreeName"),
		"description": field_text(card, "postDescription"),
		"address": field_text(card, "address"),
		"skills": text_items(card, "jobLabels"),
		"boss_name": field_text(card, "bossName"),
		"boss_title": field_text(card, "bossTitle"),
		"boss_active": field_text(card, "activeTimeDesc", "离线"),
		"security_id": security_id,
		"lid": lid or (card.get("lid") if isinstance(card.get("lid"), str) else ""),
		"raw_job_type": scalar_or_none(raw_job_type),
		"employment_type": employment_type_from_raw(raw_job_type),
		"days_per_week": field_text(card, "daysPerWeekDesc"),
		"least_month": field_text(card, "leastMonthDesc"),
		"pay_type": field_text(card, "payTypeDesc"),
		"greeted": greeted,
	}
