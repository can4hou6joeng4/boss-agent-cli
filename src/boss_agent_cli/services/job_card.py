"""把 job_card 响应映射为统一职位字段（detail / show / wizard 共用）。"""

from typing import Any

from boss_agent_cli.api.models import employment_type_from_raw, field_list, field_text


def build_job_from_card(card: dict[str, Any], *, security_id: str, greeted: bool, lid: str = "") -> dict[str, Any]:
	"""把 job_card 响应映射为统一职位字段 dict（show / detail 浏览器兜底通道共用）。"""
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
		"skills": field_list(card, "jobLabels"),
		"boss_name": field_text(card, "bossName"),
		"boss_title": field_text(card, "bossTitle"),
		"boss_active": field_text(card, "activeTimeDesc", "离线"),
		"security_id": security_id,
		"lid": lid or card.get("lid") or "",
		"raw_job_type": raw_job_type,
		"employment_type": employment_type_from_raw(raw_job_type),
		"days_per_week": field_text(card, "daysPerWeekDesc"),
		"least_month": field_text(card, "leastMonthDesc"),
		"pay_type": field_text(card, "payTypeDesc"),
		"greeted": greeted,
	}
