"""审计批次五十一：egress 语言地区解析与时区映射（Issue #2129）。

`language_region` 原先把首标签的任意尾段当地区（``zh-Hans`` → ``"HANS"``），
让正常会话被判成 mismatch 阻断启动；时区表里的 ``America/Honolulu`` 不是
有效 IANA 名，夏威夷出口矛盾永远漏检成 unknown。
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from boss_agent_cli.api.egress import evaluate_egress, language_region, timezone_region


@pytest.mark.parametrize(
	("tag", "expected"),
	[
		("zh-Hans", ""),
		("sr-Latn", ""),
		("zh-Hans-CN", "CN"),
		("en-US-u-ca-gregory", "US"),
		("x-private", ""),
		("es-419", "419"),
		("zh-CN,zh;q=0.9", "CN"),
		("ja-JP", "JP"),
		("zh", ""),
		("", ""),
	],
)
def test_language_region_reads_real_region_subtag(tag: str, expected: str) -> None:
	assert language_region(tag) == expected


def test_script_only_language_is_not_a_contradiction() -> None:
	verdict = evaluate_egress(
		exit_country="CN", accept_language="zh-Hans", timezone="Asia/Shanghai"
	)

	assert verdict.status == "ok", verdict.detail


def test_honolulu_timezone_is_a_valid_iana_name() -> None:
	ZoneInfo("Pacific/Honolulu")  # 表里的名称必须能被 tzdata 解析

	assert timezone_region("Pacific/Honolulu") == "US"


def test_honolulu_contradiction_is_detected() -> None:
	verdict = evaluate_egress(
		exit_country="JP", accept_language="ja-JP", timezone="Pacific/Honolulu"
	)

	assert verdict.status == "mismatch"
	assert verdict.refused is True
