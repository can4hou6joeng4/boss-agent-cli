"""Tests for AI prompt templates."""

from boss_agent_cli.ai.prompts import (
	JD_ANALYSIS_PROMPT,
	RESUME_OPTIMIZE_FOR_JD_PROMPT,
	RESUME_POLISH_PROMPT,
	RESUME_SUGGEST_PROMPT,
)


# ── placeholder existence ────────────────────────────────────


def test_jd_analysis_has_placeholders():
	"""JD_ANALYSIS_PROMPT contains {jd_text} and {resume_text}."""
	assert "{jd_text}" in JD_ANALYSIS_PROMPT
	assert "{resume_text}" in JD_ANALYSIS_PROMPT


def test_resume_polish_has_placeholder():
	"""RESUME_POLISH_PROMPT contains {resume_text}."""
	assert "{resume_text}" in RESUME_POLISH_PROMPT


def test_resume_optimize_has_placeholders():
	"""RESUME_OPTIMIZE_FOR_JD_PROMPT contains both placeholders."""
	assert "{jd_text}" in RESUME_OPTIMIZE_FOR_JD_PROMPT
	assert "{resume_text}" in RESUME_OPTIMIZE_FOR_JD_PROMPT


def test_resume_suggest_has_placeholders():
	"""RESUME_SUGGEST_PROMPT contains both placeholders."""
	assert "{jd_text}" in RESUME_SUGGEST_PROMPT
	assert "{resume_text}" in RESUME_SUGGEST_PROMPT


# ── format correctness ───────────────────────────────────────


def test_jd_analysis_format():
	"""JD_ANALYSIS_PROMPT can be formatted without errors."""
	result = JD_ANALYSIS_PROMPT.format(jd_text="Test JD", resume_text="Test Resume")
	assert "Test JD" in result
	assert "Test Resume" in result


def test_resume_polish_format():
	"""RESUME_POLISH_PROMPT can be formatted without errors."""
	result = RESUME_POLISH_PROMPT.format(resume_text="My Resume")
	assert "My Resume" in result


def test_resume_optimize_format():
	"""RESUME_OPTIMIZE_FOR_JD_PROMPT can be formatted without errors."""
	result = RESUME_OPTIMIZE_FOR_JD_PROMPT.format(jd_text="JD Text", resume_text="Resume Text")
	assert "JD Text" in result
	assert "Resume Text" in result


def test_resume_suggest_format():
	"""RESUME_SUGGEST_PROMPT can be formatted without errors."""
	result = RESUME_SUGGEST_PROMPT.format(jd_text="JD", resume_text="Resume")
	assert "JD" in result
	assert "Resume" in result


# ── content keyword checks ───────────────────────────────────


def test_jd_analysis_keywords():
	"""JD_ANALYSIS_PROMPT mentions match_score and JSON output."""
	assert "match_score" in JD_ANALYSIS_PROMPT
	assert "JSON" in JD_ANALYSIS_PROMPT
	assert "match_analysis" in JD_ANALYSIS_PROMPT


def test_resume_polish_mentions_star():
	"""RESUME_POLISH_PROMPT references the STAR method."""
	assert "STAR" in RESUME_POLISH_PROMPT


def test_resume_optimize_mentions_score():
	"""RESUME_OPTIMIZE_FOR_JD_PROMPT references match_score_before/after."""
	assert "match_score_before" in RESUME_OPTIMIZE_FOR_JD_PROMPT
	assert "match_score_after" in RESUME_OPTIMIZE_FOR_JD_PROMPT


def test_resume_suggest_mentions_priority():
	"""RESUME_SUGGEST_PROMPT includes priority levels."""
	assert "high" in RESUME_SUGGEST_PROMPT
	assert "medium" in RESUME_SUGGEST_PROMPT
	assert "low" in RESUME_SUGGEST_PROMPT


# ── JSON template integrity ──────────────────────────────────


def test_jd_analysis_json_template():
	"""After format, the JSON template has valid braces."""
	result = JD_ANALYSIS_PROMPT.format(jd_text="test", resume_text="test")
	# Should not contain unresolved {placeholders}, but should contain JSON { }
	assert "{" in result  # JSON structure still present
	assert "}" in result


def test_all_prompts_non_empty():
	"""All prompts are non-empty strings."""
	for prompt in [JD_ANALYSIS_PROMPT, RESUME_POLISH_PROMPT, RESUME_OPTIMIZE_FOR_JD_PROMPT, RESUME_SUGGEST_PROMPT]:
		assert isinstance(prompt, str)
		assert len(prompt) > 100
