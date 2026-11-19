"""审计批次五十五：job 去重只认 strip 后非空的字符串 ID（Issue #2140）。

纯空白 ``job_id`` 是真值字符串，会遮蔽合法的 ``encryptJobId``：
不同职位共用 ``" "`` 去重键而被合并；带空白的真实 ID 又与干净值分裂。
"""

from __future__ import annotations

from boss_agent_cli.services.job_identity import deduplicate_job_result


def test_blank_job_id_does_not_shadow_encrypt_job_id() -> None:
	items = [
		{"job_id": " ", "encryptJobId": "jobA"},
		{"job_id": " ", "encryptJobId": "jobB"},
	]

	assert deduplicate_job_result(items) == items


def test_whitespace_variant_dedupes_with_clean_id() -> None:
	items = [{"job_id": " jobA"}, {"job_id": "jobA"}]

	assert deduplicate_job_result(items) == [{"job_id": " jobA"}]


def test_blank_encrypt_job_id_falls_back_to_job_id() -> None:
	items = [
		{"job_id": "jobA", "encryptJobId": " "},
		{"job_id": "jobA", "encryptJobId": " "},
	]

	assert deduplicate_job_result(items) == [{"job_id": "jobA", "encryptJobId": " "}]


def test_padded_encrypt_job_id_is_normalized() -> None:
	items = [{"encryptJobId": " jobA "}, {"encryptJobId": "jobA"}]

	assert deduplicate_job_result(items) == [{"encryptJobId": " jobA "}]


def test_empty_job_id_still_falls_back_to_encrypt_job_id() -> None:
	items = [{"job_id": "", "encryptJobId": "jobA"}, {"job_id": "", "encryptJobId": "jobA"}]

	assert deduplicate_job_result(items) == [{"job_id": "", "encryptJobId": "jobA"}]


def test_non_string_job_id_still_falls_back() -> None:
	items = [{"job_id": 123, "encryptJobId": "jobA"}, {"job_id": 456, "encryptJobId": "jobA"}]

	assert deduplicate_job_result(items) == [{"job_id": 123, "encryptJobId": "jobA"}]


def test_items_without_any_id_are_kept() -> None:
	items = [{}, {"job_id": " ", "encryptJobId": "  "}]

	assert deduplicate_job_result(items) == items


def test_seen_job_ids_shared_across_pages() -> None:
	seen: set[str] = set()
	assert deduplicate_job_result([{"job_id": "jobA"}], seen_job_ids=seen) == [{"job_id": "jobA"}]
	assert deduplicate_job_result([{"job_id": " jobA"}], seen_job_ids=seen) == []
