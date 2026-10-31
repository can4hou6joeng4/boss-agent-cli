from dataclasses import dataclass, field
from typing import Any


BOSS_INTERNSHIP_RESPONSE_JOB_TYPE = 4


# 平台响应里的字段可能是显式的 null（"welfareList": null、"bossInfo": null）。
# dict.get(key, default) 只在键不存在时用默认值，值为 null 时照样返回 None，
# 下游一 join / 遍历 / 取子字段就崩，所以平台数据在边界处统一归一化。
def field_text(data: Any, key: str, default: str = "") -> str:
	"""取文本字段：缺失或为 null 时用默认值，数字等标量转成字符串。"""
	value = data.get(key) if isinstance(data, dict) else None
	if value is None:
		return default
	return value if isinstance(value, str) else str(value)


def field_list(data: Any, key: str) -> list[Any]:
	"""取列表字段：缺失、为 null 或不是列表时返回空列表。"""
	value = data.get(key) if isinstance(data, dict) else None
	return value if isinstance(value, list) else []


def field_dict(data: Any, key: str) -> dict[str, Any]:
	"""取对象字段：缺失、为 null 或不是对象时返回空字典。"""
	value = data.get(key) if isinstance(data, dict) else None
	return value if isinstance(value, dict) else {}


def job_public_url(job_id: str) -> str:
	"""职位详情页地址。只由 encryptJobId 拼出，不额外请求平台。"""
	if not job_id:
		return ""
	return f"https://www.zhipin.com/job_detail/{job_id}.html"


def employment_type_from_raw(raw_job_type: Any) -> str:
	"""Normalize verified BOSS response job types without guessing unknown codes."""
	if str(raw_job_type) == str(BOSS_INTERNSHIP_RESPONSE_JOB_TYPE):
		return "实习"
	return ""


def is_boss_online(value: Any) -> bool:
	"""列表项 bossOnline 归一化：True / 1 / "1"（及 "true"）视为在线，其余一律离线。

	平台在不同通道里给过布尔和数字两种形态；JobItem 和 --active 判断共用这里，
	避免一处按真值、一处按 ``is True`` 判断导致结论不一致。
	"""
	if isinstance(value, bool):
		return value
	if isinstance(value, int):
		return value == 1
	if isinstance(value, str):
		return value.strip().lower() in {"1", "true"}
	return False


def _active_desc(value: Any) -> str:
	"""列表项里的 HR 活跃度文案（如「刚刚活跃」「本周活跃」）；没有时为空串。"""
	return value.strip() if isinstance(value, str) else ""


@dataclass
class JobItem:
	job_id: str
	title: str
	company: str
	salary: str
	city: str
	district: str
	experience: str
	education: str
	skills: list[str]
	welfare: list[str]
	industry: str
	scale: str
	stage: str
	boss_name: str
	boss_title: str
	boss_active: str
	security_id: str
	greeted: bool = False
	raw_job_type: int | str | None = None
	employment_type: str = ""
	days_per_week: str = ""
	least_month: str = ""
	job_labels: list[str] = field(default_factory=list)
	lid: str = ""
	boss_active_desc: str = ""
	job_url: str = ""

	@classmethod
	def from_api(cls, raw: dict[str, Any]) -> "JobItem":
		raw_job_type = raw.get("jobType")
		return cls(
			job_id=field_text(raw, "encryptJobId"),
			job_url=job_public_url(str(raw.get("encryptJobId") or "")),
			title=field_text(raw, "jobName"),
			company=field_text(raw, "brandName"),
			salary=field_text(raw, "salaryDesc"),
			city=field_text(raw, "cityName"),
			district=field_text(raw, "areaDistrict"),
			experience=field_text(raw, "jobExperience"),
			education=field_text(raw, "jobDegree"),
			skills=field_list(raw, "skills"),
			welfare=field_list(raw, "welfareList"),
			industry=field_text(raw, "brandIndustry"),
			scale=field_text(raw, "brandScaleName"),
			stage=field_text(raw, "brandStageName"),
			boss_name=field_text(raw, "bossName"),
			boss_title=field_text(raw, "bossTitle"),
			boss_active="在线" if is_boss_online(raw.get("bossOnline")) else "离线",
			security_id=field_text(raw, "securityId"),
			lid=field_text(raw, "lid"),
			raw_job_type=raw_job_type,
			employment_type=employment_type_from_raw(raw_job_type),
			days_per_week=field_text(raw, "daysPerWeekDesc"),
			least_month=field_text(raw, "leastMonthDesc"),
			job_labels=field_list(raw, "jobLabels"),
			boss_active_desc=_active_desc(raw.get("activeTimeDesc")),
		)

	def to_dict(self) -> dict[str, Any]:
		return {
			"job_id": self.job_id,
			"title": self.title,
			"company": self.company,
			"salary": self.salary,
			"city": self.city,
			"district": self.district,
			"experience": self.experience,
			"education": self.education,
			"skills": self.skills,
			"welfare": self.welfare,
			"industry": self.industry,
			"scale": self.scale,
			"stage": self.stage,
			"boss_name": self.boss_name,
			"boss_title": self.boss_title,
			"boss_active": self.boss_active,
			"boss_active_desc": self.boss_active_desc,
			"security_id": self.security_id,
			"lid": self.lid,
			"raw_job_type": self.raw_job_type,
			"employment_type": self.employment_type,
			"days_per_week": self.days_per_week,
			"least_month": self.least_month,
			"job_labels": self.job_labels,
			"greeted": self.greeted,
			"job_url": self.job_url,
		}


@dataclass
class JobDetail:
	job_id: str
	title: str
	company: str
	salary: str
	city: str
	experience: str
	education: str
	description: str
	boss_name: str
	boss_title: str
	boss_active: str
	security_id: str
	company_info: dict[str, Any] = field(default_factory=dict)
	greeted: bool = False
	raw_job_type: int | str | None = None
	employment_type: str = ""
	days_per_week: str = ""
	least_month: str = ""
	pay_type: str = ""

	@classmethod
	def from_api(cls, raw: dict[str, Any]) -> "JobDetail":
		job_info = field_dict(raw, "jobInfo")
		boss_info = field_dict(raw, "bossInfo")
		brand_info = field_dict(raw, "brandComInfo")
		raw_job_type = job_info.get("jobType")
		return cls(
			job_id=field_text(job_info, "encryptJobId"),
			title=field_text(job_info, "jobName"),
			company=field_text(brand_info, "brandName"),
			salary=field_text(job_info, "salaryDesc"),
			city=field_text(job_info, "cityName"),
			experience=field_text(job_info, "experienceName"),
			education=field_text(job_info, "degreeName"),
			description=field_text(raw, "jobDetail"),
			boss_name=field_text(boss_info, "name"),
			boss_title=field_text(boss_info, "title"),
			boss_active=field_text(boss_info, "activeTimeDesc", "离线"),
			security_id=field_text(job_info, "securityId"),
			raw_job_type=raw_job_type,
			employment_type=employment_type_from_raw(raw_job_type),
			days_per_week=field_text(job_info, "daysPerWeekDesc"),
			least_month=field_text(job_info, "leastMonthDesc"),
			pay_type=field_text(job_info, "payTypeDesc"),
			company_info={
				"industry": field_text(brand_info, "industryName"),
				"scale": field_text(brand_info, "scaleName"),
				"stage": field_text(brand_info, "stageName"),
			},
		)

	def to_dict(self) -> dict[str, Any]:
		return {
			"job_id": self.job_id,
			"title": self.title,
			"company": self.company,
			"salary": self.salary,
			"city": self.city,
			"experience": self.experience,
			"education": self.education,
			"description": self.description,
			"boss_name": self.boss_name,
			"boss_title": self.boss_title,
			"boss_active": self.boss_active,
			"security_id": self.security_id,
			"raw_job_type": self.raw_job_type,
			"employment_type": self.employment_type,
			"days_per_week": self.days_per_week,
			"least_month": self.least_month,
			"pay_type": self.pay_type,
			"company_info": self.company_info,
			"greeted": self.greeted,
		}
