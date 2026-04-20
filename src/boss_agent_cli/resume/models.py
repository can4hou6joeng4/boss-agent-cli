from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class PersonalInfoItem:
	"""个人信息条目"""

	label: str
	value: str
	icon: str = ""
	link: str = ""


@dataclass
class PersonalInfoSection:
	"""个人信息区块"""

	items: list[PersonalInfoItem] = field(default_factory=list)
	layout: str = "inline"


@dataclass
class JobIntentionItem:
	"""求职意向条目"""

	label: str
	value: str


@dataclass
class JobIntentionSection:
	"""求职意向区块"""

	title: str = "求职意向"
	icon: str = "mdi:target"
	items: list[JobIntentionItem] = field(default_factory=list)
	show_background: bool = True


@dataclass
class TagsRow:
	"""标签行"""

	type: str = "tags"
	tags: list[str] = field(default_factory=list)


@dataclass
class RichTextRow:
	"""富文本行"""

	type: str = "richtext"
	columns: int = 1
	content: list[str] = field(default_factory=list)


@dataclass
class ResumeModule:
	"""简历模块"""

	id: str
	title: str
	icon: str = ""
	rows: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ResumeData:
	"""简历完整数据结构"""

	name: str
	title: str
	center_title: bool = False
	personal_info: PersonalInfoSection = field(default_factory=PersonalInfoSection)
	job_intention: JobIntentionSection | None = None
	modules: list[ResumeModule] = field(default_factory=list)
	avatar: str = ""
	created_at: str = ""
	updated_at: str = ""

	def __post_init__(self) -> None:
		now = datetime.now().isoformat()
		if not self.created_at:
			self.created_at = now
		if not self.updated_at:
			self.updated_at = now


@dataclass
class ResumeFile:
	"""简历文件信封格式"""

	version: str = "1.0"
	data: ResumeData | None = None
	metadata: dict[str, Any] = field(
		default_factory=lambda: {
			"exported_at": "",
			"app_version": "",
			"source": "boss-agent-cli",
		}
	)


def resume_to_dict(resume: ResumeData) -> dict[str, Any]:
	"""将 ResumeData 转为可 JSON 序列化的 dict"""
	d: dict[str, Any] = {
		"name": resume.name,
		"title": resume.title,
		"center_title": resume.center_title,
		"personal_info": {
			"items": [
				{"label": item.label, "value": item.value, "icon": item.icon, "link": item.link}
				for item in resume.personal_info.items
			],
			"layout": resume.personal_info.layout,
		},
		"job_intention": None,
		"modules": [
			{
				"id": mod.id,
				"title": mod.title,
				"icon": mod.icon,
				"rows": mod.rows,
			}
			for mod in resume.modules
		],
		"avatar": resume.avatar,
		"created_at": resume.created_at,
		"updated_at": resume.updated_at,
	}
	if resume.job_intention is not None:
		d["job_intention"] = {
			"title": resume.job_intention.title,
			"icon": resume.job_intention.icon,
			"items": [{"label": item.label, "value": item.value} for item in resume.job_intention.items],
			"show_background": resume.job_intention.show_background,
		}
	return d


def dict_to_resume(data: dict[str, Any]) -> ResumeData:
	"""从 dict 恢复 ResumeData（处理嵌套对象）"""
	pi_data = data.get("personal_info") or data.get("personalInfoSection") or {}
	pi_items = [
		PersonalInfoItem(
			label=item.get("label", ""),
			value=item.get("value", ""),
			icon=item.get("icon", ""),
			link=item.get("link", ""),
		)
		for item in pi_data.get("items", [])
	]
	personal_info = PersonalInfoSection(
		items=pi_items,
		layout=pi_data.get("layout", "inline"),
	)

	ji_data = data.get("job_intention") or data.get("jobIntentionSection")
	job_intention = None
	if ji_data is not None:
		ji_items = [
			JobIntentionItem(label=item.get("label", ""), value=item.get("value", ""))
			for item in ji_data.get("items", [])
		]
		job_intention = JobIntentionSection(
			title=ji_data.get("title", "求职意向"),
			icon=ji_data.get("icon", "mdi:target"),
			items=ji_items,
			show_background=ji_data.get("show_background", ji_data.get("showBackground", True)),
		)

	modules_data = data.get("modules", [])
	modules = [
		ResumeModule(
			id=mod.get("id", ""),
			title=mod.get("title", ""),
			icon=mod.get("icon", ""),
			rows=mod.get("rows", []),
		)
		for mod in modules_data
	]

	return ResumeData(
		name=data.get("name", ""),
		title=data.get("title", ""),
		center_title=data.get("center_title", data.get("centerTitle", False)),
		personal_info=personal_info,
		job_intention=job_intention,
		modules=modules,
		avatar=data.get("avatar", ""),
		created_at=data.get("created_at", data.get("createdAt", "")),
		updated_at=data.get("updated_at", data.get("updatedAt", "")),
	)


def resume_to_text(resume: ResumeData) -> str:
	"""将简历转为纯文本（用于 AI 输入）"""
	lines: list[str] = []

	lines.append(resume.name)
	lines.append(resume.title)
	lines.append("")

	if resume.personal_info.items:
		for item in resume.personal_info.items:
			lines.append(f"{item.label}: {item.value}")
		lines.append("")

	if resume.job_intention is not None:
		lines.append(resume.job_intention.title)
		for ji_item in resume.job_intention.items:
			lines.append(f"{ji_item.label}: {ji_item.value}")
		lines.append("")

	for mod in resume.modules:
		lines.append(mod.title)
		for row in mod.rows:
			row_type = row.get("type", "")
			if row_type == "tags":
				tags = row.get("tags", [])
				if tags:
					lines.append(", ".join(tags))
			elif row_type == "richtext":
				content = row.get("content", [])
				for line in content:
					lines.append(line)
			else:
				content = row.get("content", [])
				for line in content:
					lines.append(str(line))
		lines.append("")

	return "\n".join(lines).strip()
