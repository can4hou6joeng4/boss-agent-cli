from boss_agent_cli.api.models import JobItem
from boss_agent_cli.search_filters import (
	SearchFilterCriteria,
	meets_education_threshold,
	meets_experience_threshold,
	parse_salary_range,
)


def _extract_expect_preferences(expect_data: dict | None) -> dict:
	if not expect_data:
		return {}
	return {
		"city": expect_data.get("city") or expect_data.get("cityName") or expect_data.get("locationName"),
		"salary": expect_data.get("salary") or expect_data.get("salaryDesc"),
		"education": expect_data.get("degree") or expect_data.get("education"),
	}


def _score_salary(candidate: str, required: str | None, match_reasons: list[str], mismatch_reasons: list[str]) -> int:
	if not required:
		return 0
	candidate_range = parse_salary_range(candidate)
	required_range = parse_salary_range(required)
	if not candidate_range or not required_range:
		return 0
	if candidate_range[1] >= required_range[0]:
		match_reasons.append("薪资满足预期")
		return 25
	mismatch_reasons.append("薪资低于预期")
	return 0


def score_job_item(job: JobItem, *, criteria: SearchFilterCriteria | None, expect_data: dict | None) -> dict:
	preferences = _extract_expect_preferences(expect_data)
	match_reasons: list[str] = []
	mismatch_reasons: list[str] = []
	score = 0

	city_target = criteria.city if criteria and criteria.city else preferences.get("city")
	if city_target:
		if city_target in job.city:
			score += 25
			match_reasons.append("城市匹配")
		else:
			mismatch_reasons.append("城市不匹配")

	salary_target = criteria.salary if criteria and criteria.salary else preferences.get("salary")
	score += _score_salary(job.salary, salary_target, match_reasons, mismatch_reasons)

	exp_target = criteria.experience if criteria else None
	if exp_target:
		if meets_experience_threshold(job.experience, exp_target):
			score += 20
			match_reasons.append("经验满足要求")
		else:
			mismatch_reasons.append("经验低于要求")

	edu_target = criteria.education if criteria and criteria.education else preferences.get("education")
	if edu_target:
		if meets_education_threshold(job.education, edu_target):
			score += 15
			match_reasons.append("学历满足要求")
		else:
			mismatch_reasons.append("学历低于要求")

	if criteria and criteria.query:
		query = criteria.query.lower()
		title_text = f"{job.title} {' '.join(job.skills)}".lower()
		if query in title_text:
			score += 10
			match_reasons.append("关键词直接命中")

	if job.welfare:
		score += 5
		match_reasons.append("福利信息完整")

	return {
		"match_score": min(score, 100),
		"match_reasons": match_reasons,
		"mismatch_reasons": mismatch_reasons,
	}


def score_job_dict(item: dict, *, criteria: SearchFilterCriteria | None, expect_data: dict | None) -> dict:
	job = JobItem(
		job_id=item.get("job_id", ""),
		title=item.get("title", ""),
		company=item.get("company", ""),
		salary=item.get("salary", ""),
		city=item.get("city", ""),
		district=item.get("district", ""),
		experience=item.get("experience", ""),
		education=item.get("education", ""),
		skills=item.get("skills", []),
		welfare=item.get("welfare", []),
		industry=item.get("industry", ""),
		scale=item.get("scale", ""),
		stage=item.get("stage", ""),
		boss_name=item.get("boss_name", ""),
		boss_title=item.get("boss_title", ""),
		boss_active=item.get("boss_active", ""),
		security_id=item.get("security_id", ""),
		greeted=item.get("greeted", False),
	)
	return {**item, **score_job_item(job, criteria=criteria, expect_data=expect_data)}
