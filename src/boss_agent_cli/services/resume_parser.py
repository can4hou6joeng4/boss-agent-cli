"""招聘者 — 简历数据结构化解析。

将 BOSS 直聘 view_geek 原始响应转为干净的 JSON 结构，
方便 Agent 和 CLI 消费。
"""
from __future__ import annotations

from typing import Any


def _safe_str(val: Any) -> str:
	if val is None:
		return ""
	return str(val)


def _parse_base(info: dict[str, Any]) -> dict[str, Any]:
	base = info.get("geekBaseInfo", {})
	return {
		"name": base.get("name", ""),
		"gender": "男" if base.get("gender") == 1 else "女",
		"age": base.get("ageDesc", ""),
		"degree": base.get("degreeCategory", ""),
		"work_years": base.get("workYearDesc", ""),
		"active_status": base.get("activeTimeDesc", ""),
		"avatar": base.get("large", ""),
	}


def _parse_expect(info: dict[str, Any]) -> dict[str, Any]:
	ex = info.get("showExpectPosition") or {}
	return {
		"position": ex.get("positionName", ""),
		"salary": ex.get("salaryDesc", ""),
		"city": ex.get("locationName", ""),
	}


def _parse_works(info: dict[str, Any]) -> list[dict[str, Any]]:
	result = []
	for w in info.get("geekWorkExpList", []):
		result.append({
			"company": w.get("company", ""),
			"position": w.get("positionName", ""),
			"department": w.get("department", ""),
			"start": w.get("startYearMonStr", ""),
			"end": w.get("endYearMonStr", ""),
			"duration": w.get("workYearDesc", ""),
			"responsibility": w.get("responsibility", ""),
			"performance": w.get("workPerformance", ""),
			"keywords": w.get("workEmphasis", "").split("#&#") if w.get("workEmphasis") else [],
		})
	return result


def _parse_projects(info: dict[str, Any]) -> list[dict[str, Any]]:
	result = []
	for p in info.get("geekProjExpList", []):
		result.append({
			"name": p.get("name", ""),
			"role": p.get("roleName", ""),
			"start": p.get("startDateDesc", ""),
			"end": p.get("endDateDesc", ""),
			"duration": p.get("workYearDesc", ""),
			"description": p.get("projectDescription", ""),
			"achievement": p.get("performance", ""),
		})
	return result


def _parse_education(info: dict[str, Any]) -> list[dict[str, Any]]:
	result = []
	for e in info.get("geekEduExpList", []):
		result.append({
			"school": e.get("school", ""),
			"major": e.get("major", ""),
			"degree": e.get("degreeDesc", ""),
			"start": e.get("startYearMonStr", ""),
			"end": e.get("endYearMonStr", ""),
		})
	return result


def _parse_competitive(info: dict[str, Any]) -> list[str]:
	jc = info.get("jobCompetitive") or {}
	tips = jc.get("tips") or []
	return [t.get("content", "") for t in tips]


def parse_resume(raw: dict[str, Any]) -> dict[str, Any]:
	"""从 view_geek 响应解析结构化简历。

	Parameters
	----------
	raw : dict
		view_geek 返回的完整响应（含 code/zpData 或 code/data），
		也可直接传入已解包的数据体。

	Returns
	-------
	dict
		结构化简历：basic / expectation / work_experience /
		project_experience / education / competitive_analysis / certifications
	"""
	payload = raw.get("zpData") if "zpData" in raw else raw.get("data", raw)
	if not isinstance(payload, dict):
		payload = {}
	info = payload.get("geekDetailInfo", {})

	certs = [_safe_str(c.get("certName")) for c in info.get("geekCertificationList", []) if c.get("certName")]

	return {
		"basic": _parse_base(info),
		"expectation": _parse_expect(info),
		"work_experience": _parse_works(info),
		"project_experience": _parse_projects(info),
		"education": _parse_education(info),
		"competitive_analysis": _parse_competitive(info),
		"certifications": certs,
	}
