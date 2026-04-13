from boss_agent_cli.digest import build_digest


def test_build_digest_groups_inputs_into_sections():
	result = build_digest(
		new_matches=[{"security_id": "sec_1"}],
		follow_ups=[{"security_id": "sec_2"}],
		interviews=[{"jobName": "Go 开发"}],
	)
	assert result["new_match_count"] == 1
	assert result["follow_up_count"] == 1
	assert result["interview_count"] == 1
	assert result["new_matches"][0]["security_id"] == "sec_1"


def test_build_digest_handles_empty_sections():
	result = build_digest(new_matches=[], follow_ups=[], interviews=[])
	assert result["new_match_count"] == 0
	assert result["follow_up_count"] == 0
	assert result["interview_count"] == 0
	assert result["summary"] == "0 new matches, 0 follow-ups, 0 interviews"
