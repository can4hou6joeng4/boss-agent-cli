"""MCP 工具静态只读判定与共享白名单（Issue #2993、#2997）。

`boss schema --format mcp-tools` 与运行时 ``tools/list`` 必须给出同一份
``readOnlyHint`` 注解：本模块不导入 ``mcp``，两处都从这里取规则与白名单，
避免导出层与服务器层的只读判定各自漂移。
"""

from __future__ import annotations

from typing import Any

#: 平台请求之外不写任何用户可见本地状态、在超时 / 取消后可以安全重放的工具。
#: 未列出的工具（包括未来新增的）一律按「可能已产生平台副作用」处理：漏判只读
#: 只是保守，漏判写操作会让 Agent 直接重试一个可能已经生效的动作（Issue #968）。
READ_ONLY_TOOL_NAMES = frozenset({
	"boss_status",
	"boss_doctor",
	"boss_schema",
	"boss_search",
	"boss_recommend",
	"boss_detail",
	"boss_show",
	"boss_history",
	"boss_chat",
	"boss_chatmsg",
	"boss_me",
	"boss_interviews",
	"boss_cities",
	"boss_pipeline",
	"boss_follow_up",
	"boss_digest",
	"boss_stats",
	"boss_export",
	"boss_resume_list",
	"boss_resume_show",
	"boss_resume_diff",
	"boss_resume_applications",
	"boss_watch_list",
	"boss_preset_list",
	"boss_shortlist_list",
	"boss_shortlist_compare",
	"boss_favorites_list",
	"boss_crawl_status",
	"boss_crawl_results",
	"boss_chat_summary",
	"boss_agent_stats",
	"boss_agent_pending",
	"boss_agent_review",
	"boss_config",
	"boss_hr_whoami",
	"boss_hr_applications",
	"boss_hr_candidates",
	"boss_hr_recommendations",
	"boss_hr_chat",
	"boss_hr_chatmsg",
	"boss_hr_last_messages",
	"boss_hr_resume",
	"boss_hr_jobs",
	"boss_hr_jobs_detail",
	# wizard 只有 status 是纯读取，其余 action 按写操作处理（Issue #2552）。
	"boss_wizard",
})

#: 本地输出参数：工具即使只读平台，带这些参数时也会写本机文件，不能标成
#: ``readOnlyHint=true``。宿主可能对只读调用自动放行，误标会把本地文件写入
#: 藏进「只读」提示里（Issue #2993）。
LOCAL_OUTPUT_ARGUMENT_KEYS = frozenset({"output", "output_file"})


def static_read_only(tool_name: str, input_schema: dict[str, Any]) -> bool:
	"""返回工具在所有参数组合下是否都只读（供 MCP ``ToolAnnotations`` 使用）。

	只用于静态注解：带 ``action`` 维度的工具（``boss_config`` / ``boss_hr_jobs``
	/ ``boss_wizard``）写语义取决于动作，带 ``output`` / ``output_file`` 的工具
	可能写本地文件，都不能标为只读。动态写判定仍由 ``mcp_server._tool_may_write``
	按参数负责，两者互不替代。「只读」指不产生平台写入或用户可见的本地状态变更；
	缓存与可写性探针不计。
	"""
	if tool_name not in READ_ONLY_TOOL_NAMES:
		return False
	properties = input_schema.get("properties", {})
	if not isinstance(properties, dict) or "action" in properties:
		return False
	return not (LOCAL_OUTPUT_ARGUMENT_KEYS & set(properties))
