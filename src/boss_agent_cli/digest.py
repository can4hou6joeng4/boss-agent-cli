def build_digest(*, new_matches: list[dict], follow_ups: list[dict], interviews: list[dict]) -> dict:
	return {
		"new_match_count": len(new_matches),
		"follow_up_count": len(follow_ups),
		"interview_count": len(interviews),
		"new_matches": new_matches,
		"follow_ups": follow_ups,
		"interviews": interviews,
		"summary": f"{len(new_matches)} new matches, {len(follow_ups)} follow-ups, {len(interviews)} interviews",
	}
