"""把 job_card 响应映射为统一职位字段（detail / show / wizard 共用）。"""

from typing import Any

from boss_agent_cli.api.models import employment_type_from_raw


def build_job_from_card(card: dict[str, Any], *, security_id: str, greeted: bool) -> dict[str, Any]:
	"""把 job_card 响应映射为统一职位字段 dict（show / detail 浏览器兜底通道共用）。"""
	raw_job_type = card.get("jobType")
	return {
		"job_id": card.get("encryptJobId", ""),
		"title": card.get("jobName", ""),
		"company": card.get("brandName", ""),
		"salary": card.get("salaryDesc", ""),
		"city": card.get("cityName", ""),
		"experience": card.get("experienceName", ""),
		"education": card.get("degreeName", ""),
		"description": card.get("postDescription", ""),
		"address": card.get("address", ""),
		"skills": card.get("jobLabels", []),
		"boss_name": card.get("bossName", ""),
		"boss_title": card.get("bossTitle", ""),
		"boss_active": card.get("activeTimeDesc", "离线"),
		"security_id": security_id,
		"raw_job_type": raw_job_type,
		"employment_type": employment_type_from_raw(raw_job_type),
		"days_per_week": card.get("daysPerWeekDesc", ""),
		"least_month": card.get("leastMonthDesc", ""),
		"pay_type": card.get("payTypeDesc", ""),
		"greeted": greeted,
	}
