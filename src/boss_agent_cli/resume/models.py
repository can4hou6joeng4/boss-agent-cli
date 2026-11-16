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


class ResumeDataError(ValueError):
	"""本地简历 JSON 的字段类型不符合模型；消息给出字段路径与期望类型。"""


_MISSING: Any = object()


def _json_type(value: Any) -> str:
	"""按 JSON 术语描述实际类型，让修复指引不出现 Python 类型名。"""
	if isinstance(value, dict):
		return "JSON 对象"
	if isinstance(value, list):
		return "JSON 数组"
	if isinstance(value, bool):
		return "布尔值"
	if value is None:
		return "null"
	if isinstance(value, (int, float)):
		return "数字"
	if isinstance(value, str):
		return "字符串"
	return type(value).__name__


def _pick(data: dict[str, Any], *keys: str) -> Any:
	for key in keys:
		if key in data:
			return data[key]
	return _MISSING


def _expect_str(data: dict[str, Any], path: str, *keys: str, default: str = "") -> str:
	value = _pick(data, *keys)
	if value is _MISSING:
		return default
	if not isinstance(value, str):
		raise ResumeDataError(f"字段 {path} 必须是字符串，实际是 {_json_type(value)}")
	return value


def _expect_bool(data: dict[str, Any], path: str, *keys: str, default: bool) -> bool:
	value = _pick(data, *keys)
	if value is _MISSING:
		return default
	if not isinstance(value, bool):
		raise ResumeDataError(f"字段 {path} 必须是布尔值，实际是 {_json_type(value)}")
	return value


def _expect_object(value: Any, path: str) -> dict[str, Any]:
	if value is _MISSING or value is None:
		return {}
	if not isinstance(value, dict):
		raise ResumeDataError(f"字段 {path} 必须是 JSON 对象，实际是 {_json_type(value)}")
	return value


def _expect_object_list(value: Any, path: str) -> list[dict[str, Any]]:
	if value is _MISSING or value is None:
		return []
	if not isinstance(value, list):
		raise ResumeDataError(f"字段 {path} 必须是 JSON 数组，实际是 {_json_type(value)}")
	for index, item in enumerate(value):
		if not isinstance(item, dict):
			raise ResumeDataError(f"字段 {path}[{index}] 必须是 JSON 对象，实际是 {_json_type(item)}")
	return value


def dict_to_resume(data: dict[str, Any]) -> ResumeData:
	"""从 dict 恢复 ResumeData（处理嵌套对象）。

	字段类型漂移在读取边界统一拒绝：``name`` 写成对象、``rows`` 元素写成字符串
	时直接抛 ``ResumeDataError``，而不是让错误类型流进信封、或到 HTML 模板里
	才崩成 ``EXPORT_FAILED``（Issue #1189）。
	"""
	pi_data = _expect_object(_pick(data, "personal_info", "personalInfoSection"), "personal_info")
	pi_items = [
		PersonalInfoItem(
			label=_expect_str(item, f"personal_info.items[{index}].label", "label"),
			value=_expect_str(item, f"personal_info.items[{index}].value", "value"),
			icon=_expect_str(item, f"personal_info.items[{index}].icon", "icon"),
			link=_expect_str(item, f"personal_info.items[{index}].link", "link"),
		)
		for index, item in enumerate(_expect_object_list(pi_data.get("items"), "personal_info.items"))
	]
	personal_info = PersonalInfoSection(
		items=pi_items,
		layout=_expect_str(pi_data, "personal_info.layout", "layout", default="inline"),
	)

	ji_value = _pick(data, "job_intention", "jobIntentionSection")
	job_intention: JobIntentionSection | None = None
	if ji_value is not _MISSING and ji_value is not None:
		ji_data = _expect_object(ji_value, "job_intention")
		ji_items = [
			JobIntentionItem(
				label=_expect_str(item, f"job_intention.items[{index}].label", "label"),
				value=_expect_str(item, f"job_intention.items[{index}].value", "value"),
			)
			for index, item in enumerate(_expect_object_list(ji_data.get("items"), "job_intention.items"))
		]
		job_intention = JobIntentionSection(
			title=_expect_str(ji_data, "job_intention.title", "title", default="求职意向"),
			icon=_expect_str(ji_data, "job_intention.icon", "icon", default="mdi:target"),
			items=ji_items,
			show_background=_expect_bool(
				ji_data, "job_intention.show_background", "show_background", "showBackground", default=True
			),
		)

	modules_data = _expect_object_list(_pick(data, "modules"), "modules")
	modules = [
		ResumeModule(
			id=_expect_str(mod, f"modules[{index}].id", "id"),
			title=_expect_str(mod, f"modules[{index}].title", "title"),
			icon=_expect_str(mod, f"modules[{index}].icon", "icon"),
			rows=_expect_object_list(mod.get("rows"), f"modules[{index}].rows"),
		)
		for index, mod in enumerate(modules_data)
	]

	return ResumeData(
		name=_expect_str(data, "name", "name"),
		title=_expect_str(data, "title", "title"),
		center_title=_expect_bool(data, "center_title", "center_title", "centerTitle", default=False),
		personal_info=personal_info,
		job_intention=job_intention,
		modules=modules,
		avatar=_expect_str(data, "avatar", "avatar"),
		created_at=_expect_str(data, "created_at", "created_at", "createdAt"),
		updated_at=_expect_str(data, "updated_at", "updated_at", "updatedAt"),
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
