from dataclasses import dataclass, field
from typing import Any


BOSS_INTERNSHIP_RESPONSE_JOB_TYPE = 4


def employment_type_from_raw(raw_job_type: Any) -> str:
	"""Normalize verified BOSS response job types without guessing unknown codes."""
	if str(raw_job_type) == str(BOSS_INTERNSHIP_RESPONSE_JOB_TYPE):
		return "实习"
	return ""


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

	@classmethod
	def from_api(cls, raw: dict[str, Any]) -> "JobItem":
		raw_job_type = raw.get("jobType")
		return cls(
			job_id=raw.get("encryptJobId", ""),
			title=raw.get("jobName", ""),
			company=raw.get("brandName", ""),
			salary=raw.get("salaryDesc", ""),
			city=raw.get("cityName", ""),
			district=raw.get("areaDistrict", ""),
			experience=raw.get("jobExperience", ""),
			education=raw.get("jobDegree", ""),
			skills=raw.get("skills", []),
			welfare=raw.get("welfareList", []),
			industry=raw.get("brandIndustry", ""),
			scale=raw.get("brandScaleName", ""),
			stage=raw.get("brandStageName", ""),
			boss_name=raw.get("bossName", ""),
			boss_title=raw.get("bossTitle", ""),
			boss_active="在线" if raw.get("bossOnline") else "离线",
			security_id=raw.get("securityId", ""),
			raw_job_type=raw_job_type,
			employment_type=employment_type_from_raw(raw_job_type),
			days_per_week=raw.get("daysPerWeekDesc", ""),
			least_month=raw.get("leastMonthDesc", ""),
			job_labels=raw.get("jobLabels", []),
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
			"security_id": self.security_id,
			"raw_job_type": self.raw_job_type,
			"employment_type": self.employment_type,
			"days_per_week": self.days_per_week,
			"least_month": self.least_month,
			"job_labels": self.job_labels,
			"greeted": self.greeted,
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
		job_info = raw.get("jobInfo", {})
		boss_info = raw.get("bossInfo", {})
		brand_info = raw.get("brandComInfo", {})
		raw_job_type = job_info.get("jobType")
		return cls(
			job_id=job_info.get("encryptJobId", ""),
			title=job_info.get("jobName", ""),
			company=brand_info.get("brandName", ""),
			salary=job_info.get("salaryDesc", ""),
			city=job_info.get("cityName", ""),
			experience=job_info.get("experienceName", ""),
			education=job_info.get("degreeName", ""),
			description=raw.get("jobDetail", ""),
			boss_name=boss_info.get("name", ""),
			boss_title=boss_info.get("title", ""),
			boss_active=boss_info.get("activeTimeDesc", "离线"),
			security_id=job_info.get("securityId", ""),
			raw_job_type=raw_job_type,
			employment_type=employment_type_from_raw(raw_job_type),
			days_per_week=job_info.get("daysPerWeekDesc", ""),
			least_month=job_info.get("leastMonthDesc", ""),
			pay_type=job_info.get("payTypeDesc", ""),
			company_info={
				"industry": brand_info.get("industryName", ""),
				"scale": brand_info.get("scaleName", ""),
				"stage": brand_info.get("stageName", ""),
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
