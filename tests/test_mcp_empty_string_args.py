"""MCP 工具必须原样下发显式空串（Issue #1324）。

``mcp_args._build_args`` 是 MCP 与 CLI 之间唯一的参数映射层。自由文本选项
用真值判断时，``""`` 会被化装成「没传」，CLI 落到自己的默认值（``manual`` /
``manual-stop`` / ``human-rejected``），与调用方显式参数不一致；而等价的
CLI 调用 ``--source ""`` 会存空串。
"""

from boss_agent_cli.mcp_args import _build_args


def test_shortlist_add_forwards_explicit_empty_source():
	argv = _build_args("boss_shortlist_add", {
		"security_id": "sec_1",
		"job_id": "job_1",
		"source": "",
	})
	assert argv[argv.index("--source") + 1] == ""


def test_shortlist_add_omits_source_when_absent():
	argv = _build_args("boss_shortlist_add", {"security_id": "sec_1", "job_id": "job_1"})
	assert "--source" not in argv


def test_agent_stop_forwards_explicit_empty_reason():
	argv = _build_args("boss_agent_stop", {"reason": ""})
	assert argv[argv.index("--reason") + 1] == ""


def test_agent_stop_omits_reason_when_absent():
	assert "--reason" not in _build_args("boss_agent_stop", {})


def test_agent_review_reject_forwards_explicit_empty_reason():
	argv = _build_args("boss_agent_review_reject", {"id": 7, "reason": ""})
	assert argv[argv.index("--reason") + 1] == ""
	assert "7" in argv


def test_agent_review_reject_omits_reason_when_absent():
	assert "--reason" not in _build_args("boss_agent_review_reject", {"id": 7})


def test_shortlist_compare_forwards_explicit_empty_tag():
	"""Issue #1565：空 tag 必须交给 CLI 拒绝，不能在 MCP 层化装成「没传」。"""
	argv = _build_args("boss_shortlist_compare", {"tag": ""})
	assert argv[argv.index("--tag") + 1] == ""


def test_shortlist_compare_omits_tag_when_absent_or_null():
	assert "--tag" not in _build_args("boss_shortlist_compare", {})
	assert "--tag" not in _build_args("boss_shortlist_compare", {"tag": None})


def test_shortlist_annotate_forwards_explicit_empty_tag_element():
	argv = _build_args("boss_shortlist_annotate", {
		"security_id": "sec_1",
		"job_id": "job_1",
		"add_tags": [""],
	})
	assert argv[argv.index("--add-tag") + 1] == ""
