from boss_agent_cli.resume.models import (
	PersonalInfoItem,
	PersonalInfoSection,
	JobIntentionItem,
	JobIntentionSection,
	ResumeData,
	ResumeFile,
	ResumeModule,
	resume_to_dict,
	dict_to_resume,
	resume_to_text,
)


def test_resume_data_defaults():
	r = ResumeData(name="张三", title="Python 开发工程师")
	assert r.name == "张三"
	assert r.title == "Python 开发工程师"
	assert r.center_title is False
	assert r.avatar == ""
	assert r.created_at != ""
	assert r.updated_at != ""
	assert r.modules == []
	assert r.job_intention is None


def test_resume_data_auto_timestamps():
	r = ResumeData(name="张三", title="开发")
	assert r.created_at == r.updated_at
	assert "T" in r.created_at


def test_resume_data_preserves_explicit_timestamps():
	r = ResumeData(name="张三", title="开发", created_at="2026-01-01T00:00:00", updated_at="2026-01-02T00:00:00")
	assert r.created_at == "2026-01-01T00:00:00"
	assert r.updated_at == "2026-01-02T00:00:00"


def test_personal_info_item():
	item = PersonalInfoItem(label="邮箱", value="test@example.com", icon="mdi:email", link="mailto:test@example.com")
	assert item.label == "邮箱"
	assert item.value == "test@example.com"
	assert item.icon == "mdi:email"
	assert item.link == "mailto:test@example.com"


def test_personal_info_item_defaults():
	item = PersonalInfoItem(label="手机", value="13800138000")
	assert item.icon == ""
	assert item.link == ""


def test_personal_info_section():
	section = PersonalInfoSection(
		items=[PersonalInfoItem(label="手机", value="13800138000")],
		layout="grid",
	)
	assert len(section.items) == 1
	assert section.layout == "grid"


def test_personal_info_section_defaults():
	section = PersonalInfoSection()
	assert section.items == []
	assert section.layout == "inline"


def test_job_intention_section():
	section = JobIntentionSection(
		title="求职意向",
		icon="mdi:target",
		items=[
			JobIntentionItem(label="期望职位", value="Python 开发"),
			JobIntentionItem(label="期望城市", value="广州"),
		],
		show_background=True,
	)
	assert section.title == "求职意向"
	assert len(section.items) == 2
	assert section.items[0].value == "Python 开发"


def test_resume_module():
	module = ResumeModule(
		id="work_experience",
		title="工作经历",
		icon="mdi:briefcase",
		rows=[
			{"type": "richtext", "columns": 1, "content": ["5 年 Python 开发经验"]},
			{"type": "tags", "tags": ["Python", "Django", "FastAPI"]},
		],
	)
	assert module.id == "work_experience"
	assert module.title == "工作经历"
	assert len(module.rows) == 2


def test_resume_file_envelope():
	r = ResumeData(name="张三", title="开发")
	f = ResumeFile(data=r)
	assert f.version == "1.0"
	assert f.data is not None
	assert f.data.name == "张三"
	assert f.metadata["source"] == "boss-agent-cli"


def test_resume_to_dict_serialization():
	r = ResumeData(
		name="张三",
		title="Python 开发",
		center_title=True,
		personal_info=PersonalInfoSection(
			items=[PersonalInfoItem(label="邮箱", value="test@example.com")],
			layout="inline",
		),
		job_intention=JobIntentionSection(
			items=[JobIntentionItem(label="期望城市", value="广州")],
		),
		modules=[
			ResumeModule(
				id="skills",
				title="技能",
				rows=[{"type": "tags", "tags": ["Python", "Go"]}],
			),
		],
	)
	d = resume_to_dict(r)
	assert d["name"] == "张三"
	assert d["center_title"] is True
	assert len(d["personal_info"]["items"]) == 1
	assert d["personal_info"]["items"][0]["label"] == "邮箱"
	assert d["job_intention"]["items"][0]["value"] == "广州"
	assert d["modules"][0]["id"] == "skills"
	assert d["modules"][0]["rows"][0]["tags"] == ["Python", "Go"]


def test_dict_to_resume_roundtrip():
	original = ResumeData(
		name="李四",
		title="全栈工程师",
		personal_info=PersonalInfoSection(
			items=[
				PersonalInfoItem(label="手机", value="13900139000"),
				PersonalInfoItem(label="邮箱", value="li@example.com", icon="mdi:email"),
			],
		),
		job_intention=JobIntentionSection(
			items=[JobIntentionItem(label="薪资", value="20-30K")],
		),
		modules=[
			ResumeModule(id="edu", title="教育经历", icon="mdi:school"),
		],
	)
	d = resume_to_dict(original)
	restored = dict_to_resume(d)
	assert restored.name == original.name
	assert restored.title == original.title
	assert len(restored.personal_info.items) == 2
	assert restored.personal_info.items[1].icon == "mdi:email"
	assert restored.job_intention is not None
	assert restored.job_intention.items[0].value == "20-30K"
	assert restored.modules[0].id == "edu"
	assert restored.modules[0].icon == "mdi:school"


def test_dict_to_resume_without_optional_fields():
	d = {"name": "王五", "title": "前端工程师"}
	r = dict_to_resume(d)
	assert r.name == "王五"
	assert r.job_intention is None
	assert r.modules == []
	assert r.personal_info.items == []


def test_resume_to_text():
	r = ResumeData(
		name="张三",
		title="Python 开发工程师",
		personal_info=PersonalInfoSection(
			items=[
				PersonalInfoItem(label="邮箱", value="test@example.com"),
				PersonalInfoItem(label="手机", value="13800138000"),
			],
		),
		job_intention=JobIntentionSection(
			items=[
				JobIntentionItem(label="期望职位", value="Python 开发"),
				JobIntentionItem(label="期望城市", value="广州"),
			],
		),
		modules=[
			ResumeModule(
				id="skills",
				title="技能特长",
				rows=[
					{"type": "tags", "tags": ["Python", "Go", "Rust"]},
					{"type": "richtext", "content": ["精通 Python 后端开发"]},
				],
			),
		],
	)
	text = resume_to_text(r)
	assert "张三" in text
	assert "Python 开发工程师" in text
	assert "test@example.com" in text
	assert "期望职位" in text
	assert "技能特长" in text
	assert "Python" in text
	assert "精通 Python 后端开发" in text


def test_resume_to_dict_no_job_intention():
	r = ResumeData(name="张三", title="开发")
	d = resume_to_dict(r)
	assert d["job_intention"] is None
