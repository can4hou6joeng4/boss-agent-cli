"""Candidate scoring for recruiter automation."""

from __future__ import annotations

from typing import Final

from boss_agent_cli.automation.models import CandidateSnapshot, MatchScore

_EDUCATION_RANK: Final = {
	"不限": 0,
	"初中": 1,
	"高中": 2,
	"中专": 2,
	"大专": 3,
	"本科": 4,
	"硕士": 5,
	"博士": 6,
}


def score_candidate(candidate: CandidateSnapshot) -> MatchScore:
	"""Score a candidate with a compact default sales/service-oriented rubric."""
	risk_flags = set(candidate.risk_flags)
	hard_pass = not candidate.do_not_contact
	if candidate.do_not_contact:
		risk_flags.add("do-not-contact")

	text = " ".join([
		candidate.name,
		candidate.title,
		candidate.resume_text,
		" ".join(candidate.intent_signals),
	]).lower()
	keyword = _keyword_score(("销售", "客服", "运营", "客户", "沟通", "面谈"), text)
	city = 1.0 if candidate.city in {"上海", "北京", "广州", "深圳", "杭州"} else 0.5
	experience = _experience_score(candidate.experience_years)
	education = _education_score(candidate.education)
	active = 1.0 if candidate.last_active_at else 0.6
	intent = _intent_score(candidate.intent_signals)
	score = round(
		keyword * 30
		+ city * 15
		+ experience * 18
		+ education * 14
		+ active * 8
		+ intent * 15
	)
	if not hard_pass:
		score = min(score, 59)
	recommendation = (
		"invite-to-interview"
		if hard_pass and score >= 70
		else ("review" if hard_pass else "reject")
	)
	return MatchScore(
		pass_hard_conditions=hard_pass,
		score=score,
		recommendation=recommendation,
		reason=(
			f"hard={'pass' if hard_pass else 'fail'}; keyword={keyword:.2f}, "
			f"city={city:.2f}, experience={experience:.2f}, "
			f"education={education:.2f}, active={active:.2f}, intent={intent:.2f}"
		),
		risk_flags=tuple(sorted(risk_flags)),
	)


def _keyword_score(keywords: tuple[str, ...], text: str) -> float:
	hits = sum(1 for keyword in keywords if keyword.lower() in text)
	return hits / len(keywords)


def _experience_score(years: float | None) -> float:
	if years is None:
		return 0.5
	if years < 1 or years > 10:
		return 0.2
	if 2 <= years <= 5:
		return 1.0
	return 0.7


def _education_score(education: str) -> float:
	if not education:
		return 0.5
	return (
		1.0
		if _EDUCATION_RANK.get(education, 0) >= _EDUCATION_RANK["大专"]
		else 0.3
	)


def _intent_score(signals: tuple[str, ...]) -> float:
	text = " ".join(signals)
	if any(token in text for token in ("想看机会", "可面试", "近期到岗", "有兴趣")):
		return 1.0
	if any(token in text for token in ("暂不考虑", "观望", "在职")):
		return 0.25
	return 0.5
