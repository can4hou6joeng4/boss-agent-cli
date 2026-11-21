"""招聘者 — 简历数据结构化解析。

将 BOSS 直聘 view_geek 原始响应转为干净的 JSON 结构，
方便 Agent 和 CLI 消费。
"""
from __future__ import annotations

from typing import Any

from boss_agent_cli.api.models import dict_items, field_dict, field_list


def _safe_str(val: Any) -> str:
	"""文本字段恒为 str：非字符串（含 null / 数字 / dict / list）归空串。

	类型漂移原样透传或 ``str()`` 成 Python repr 都会进导出与 AI 输入
	（Issue #1808）。
	"""
	return val if isinstance(val, str) else ""


def _parse_base(info: dict[str, Any]) -> dict[str, Any]:
	base = field_dict(info, "geekBaseInfo")
	gender = base.get("gender")
	# 未提供或未识别的性别保持未知；bool 与数值编码相等，不能据此补全个人属性。
	gender_label = {0: "女", 1: "男"}.get(gender, "") if type(gender) is int else ""
	return {
		"name": _safe_str(base.get("name")),
		"gender": gender_label,
		"age": _safe_str(base.get("ageDesc")),
		"degree": _safe_str(base.get("degreeCategory")),
		"work_years": _safe_str(base.get("workYearDesc")),
		"active_status": _safe_str(base.get("activeTimeDesc")),
		"avatar": _safe_str(base.get("large")),
	}


def _parse_expect(info: dict[str, Any]) -> dict[str, Any]:
	ex = field_dict(info, "showExpectPosition")
	return {
		"position": _safe_str(ex.get("positionName")),
		"salary": _safe_str(ex.get("salaryDesc")),
		"city": _safe_str(ex.get("locationName")),
	}


def _parse_works(info: dict[str, Any]) -> list[dict[str, Any]]:
	result = []
	for w in dict_items(field_list(info, "geekWorkExpList")):
		result.append({
			"company": _safe_str(w.get("company")),
			"position": _safe_str(w.get("positionName")),
			"department": _safe_str(w.get("department")),
			"start": _safe_str(w.get("startYearMonStr")),
			"end": _safe_str(w.get("endYearMonStr")),
			"duration": _safe_str(w.get("workYearDesc")),
			"responsibility": _safe_str(w.get("responsibility")),
			"performance": _safe_str(w.get("workPerformance")),
			"keywords": [k for k in _safe_str(w.get("workEmphasis")).split("#&#") if k],
		})
	return result


def _parse_projects(info: dict[str, Any]) -> list[dict[str, Any]]:
	result = []
	for p in dict_items(field_list(info, "geekProjExpList")):
		result.append({
			"name": _safe_str(p.get("name")),
			"role": _safe_str(p.get("roleName")),
			"start": _safe_str(p.get("startDateDesc")),
			"end": _safe_str(p.get("endDateDesc")),
			"duration": _safe_str(p.get("workYearDesc")),
			"description": _safe_str(p.get("projectDescription")),
			"achievement": _safe_str(p.get("performance")),
		})
	return result


def _parse_education(info: dict[str, Any]) -> list[dict[str, Any]]:
	result = []
	for e in dict_items(field_list(info, "geekEduExpList")):
		result.append({
			"school": _safe_str(e.get("school")),
			"major": _safe_str(e.get("major")),
			"degree": _safe_str(e.get("degreeDesc") or e.get("degreeName")),
			"start": _safe_str(e.get("startYearMonStr") or e.get("startDateDesc")),
			"end": _safe_str(e.get("endYearMonStr") or e.get("endDateDesc")),
		})
	return result


def _parse_competitive(info: dict[str, Any]) -> list[str]:
	jc = field_dict(info, "jobCompetitive")
	return [_safe_str(t.get("content")) for t in dict_items(jc.get("tips"))]


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
	# `zpData` 为 null 时不能就此判空：旧格式把数据放在 `data` 里，null 遮蔽
	# 有效数据会让整份简历解析成空（Issue #1808）。空对象 `{}` 同样是「无内容」，
	# 不能让旧格式 `data` 里的整份简历被静默丢弃（Issue #2433）。
	payload: Any = None
	if isinstance(raw, dict):
		candidate = raw.get("zpData")
		if not isinstance(candidate, dict) or not candidate:
			candidate = raw.get("data", raw)
		payload = candidate
	if not isinstance(payload, dict):
		payload = {}
	info = field_dict(payload, "geekDetailInfo")

	certs = [
		name
		for c in dict_items(field_list(info, "geekCertificationList"))
		# 只留字符串证书名：类型漂移的元素不进列表（Issue #1808）。
		if isinstance((name := c.get("certName")), str) and name
	]

	return {
		"basic": _parse_base(info),
		"expectation": _parse_expect(info),
		"work_experience": _parse_works(info),
		"project_experience": _parse_projects(info),
		"education": _parse_education(info),
		"competitive_analysis": _parse_competitive(info),
		"certifications": certs,
	}
