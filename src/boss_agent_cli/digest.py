import time


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


def _escape_md_cell(text: object) -> str:
	"""Markdown 表格单元格内的 | 和换行转义，防止破坏表格结构。"""
	s = "" if text is None else str(text)
	return s.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _fmt_follow_up(item: dict) -> str:
	company = _escape_md_cell(item.get("company") or "-")
	title = _escape_md_cell(item.get("title") or "-")
	stage = _escape_md_cell(item.get("stage") or "")
	relation = _escape_md_cell(item.get("relation") or "")
	unread = item.get("unread") or 0
	last_msg = _escape_md_cell(item.get("last_msg") or "")
	last_time = _escape_md_cell(item.get("last_time") or "")
	reason = _escape_md_cell(item.get("reason") or "")
	header_parts = [f"**{company}**"]
	if title and title != "-":
		header_parts.append(f"· {title}")
	if relation:
		header_parts.append(f"· {relation}")
	if stage:
		header_parts.append(f"· 阶段：{stage}")
	if unread:
		header_parts.append(f"· 未读 {unread}")
	if last_time:
		header_parts.append(f"· {last_time}")
	line = "- " + " ".join(header_parts)
	if reason:
		line += f"\n  原因：{reason}"
	if last_msg and last_msg != "-":
		line += f"\n  > {last_msg}"
	return line


def _fmt_new_match(item: dict) -> str:
	company = _escape_md_cell(item.get("company") or "-")
	title = _escape_md_cell(item.get("title") or "-")
	relation = _escape_md_cell(item.get("relation") or "")
	unread = item.get("unread") or 0
	last_msg = _escape_md_cell(item.get("last_msg") or "")
	last_time = _escape_md_cell(item.get("last_time") or "")
	header = f"**{company}**"
	if title and title != "-":
		header += f" · {title}"
	if relation:
		header += f" · {relation}"
	if unread:
		header += f" · 未读 {unread}"
	if last_time:
		header += f" · {last_time}"
	line = f"- {header}"
	if last_msg and last_msg != "-":
		line += f"\n  > {last_msg}"
	return line


def _fmt_interview(item: dict) -> str:
	company = _escape_md_cell(item.get("company") or "-")
	title = _escape_md_cell(item.get("title") or "-")
	interview_time = _escape_md_cell(item.get("last_time") or "")
	status = _escape_md_cell(item.get("last_msg") or "")
	parts = [f"**{title}**"]
	if company and company != "-":
		parts.append(f"（{company}）")
	if interview_time:
		parts.append(f"· {interview_time}")
	if status and status != "-":
		parts.append(f"· {status}")
	return "- " + " ".join(parts)


def render_digest_markdown(data: dict, *, generated_at: str | None = None) -> str:
	"""把 build_digest 产出的结构化数据渲染为 Markdown 文本，可直接发邮件/飞书。"""
	if generated_at is None:
		generated_at = time.strftime("%Y-%m-%d %H:%M:%S")

	new_matches = data.get("new_matches") or []
	follow_ups = data.get("follow_ups") or []
	interviews = data.get("interviews") or []

	lines: list[str] = []
	lines.append("# 每日求职摘要")
	lines.append("")
	lines.append(f"_生成时间：{generated_at}_")
	lines.append("")

	lines.append("## 核心指标")
	lines.append("")
	lines.append("| 维度 | 数量 |")
	lines.append("|------|------|")
	lines.append(f"| 新匹配（待回复） | {len(new_matches)} |")
	lines.append(f"| 待跟进 | {len(follow_ups)} |")
	lines.append(f"| 面试 | {len(interviews)} |")
	lines.append("")

	lines.append("## 新匹配")
	lines.append("")
	if not new_matches:
		lines.append("_暂无新匹配_")
	else:
		for item in new_matches:
			lines.append(_fmt_new_match(item))
	lines.append("")

	lines.append("## 待跟进")
	lines.append("")
	if not follow_ups:
		lines.append("_暂无待跟进_")
	else:
		for item in follow_ups:
			lines.append(_fmt_follow_up(item))
	lines.append("")

	lines.append("## 面试")
	lines.append("")
	if not interviews:
		lines.append("_暂无面试_")
	else:
		for item in interviews:
			lines.append(_fmt_interview(item))
	lines.append("")

	lines.append("---")
	lines.append("")
	lines.append("_由 `boss digest --format md` 生成 · [boss-agent-cli](https://github.com/can4hou6joeng4/boss-agent-cli)_")
	lines.append("")

	return "\n".join(lines)
