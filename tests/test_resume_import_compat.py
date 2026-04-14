import json

from boss_agent_cli.resume.store import ResumeStore


def _write_json(path, data):
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_import_camelcase_format(tmp_path):
	"""导入 wzdnzd/zine0 camelCase 格式"""
	store = ResumeStore(tmp_path / "resumes")
	src = tmp_path / "input.json"
	_write_json(src, {
		"name": "张三",
		"title": "Python 开发",
		"centerTitle": True,
		"personalInfoSection": {
			"items": [
				{"label": "邮箱", "value": "test@example.com", "icon": "mdi:email", "link": ""},
			],
			"layout": "inline",
		},
		"jobIntentionSection": {
			"title": "求职意向",
			"icon": "mdi:target",
			"items": [{"label": "期望城市", "value": "广州"}],
			"showBackground": True,
		},
		"modules": [
			{
				"id": "skills",
				"title": "技能",
				"icon": "",
				"rows": [{"type": "tags", "tags": ["Python", "Go"]}],
			},
		],
		"avatar": "",
		"createdAt": "2026-01-01T00:00:00",
		"updatedAt": "2026-01-02T00:00:00",
	})
	r = store.import_file(src)
	assert r.name == "张三"
	assert r.center_title is True
	assert r.personal_info.items[0].label == "邮箱"
	assert r.job_intention is not None
	assert r.job_intention.items[0].value == "广州"
	assert r.modules[0].id == "skills"
	assert store.exists("张三")


def test_import_snake_case_format(tmp_path):
	"""导入本地 snake_case 格式"""
	store = ResumeStore(tmp_path / "resumes")
	src = tmp_path / "input.json"
	_write_json(src, {
		"name": "李四",
		"title": "Go 开发",
		"center_title": False,
		"personal_info": {
			"items": [{"label": "手机", "value": "13900139000"}],
			"layout": "inline",
		},
		"modules": [],
	})
	r = store.import_file(src)
	assert r.name == "李四"
	assert r.center_title is False
	assert r.personal_info.items[0].value == "13900139000"


def test_import_with_envelope(tmp_path):
	"""导入带 ResumeFile 信封的格式"""
	store = ResumeStore(tmp_path / "resumes")
	src = tmp_path / "input.json"
	_write_json(src, {
		"version": "1.0",
		"data": {
			"name": "王五",
			"title": "前端工程师",
			"personal_info": {"items": [], "layout": "inline"},
			"modules": [],
		},
		"metadata": {"source": "other-tool"},
	})
	r = store.import_file(src)
	assert r.name == "王五"
	assert r.title == "前端工程师"
	assert store.exists("王五")


def test_import_bare_resume_data(tmp_path):
	"""导入无信封的裸 ResumeData 格式"""
	store = ResumeStore(tmp_path / "resumes")
	src = tmp_path / "input.json"
	_write_json(src, {
		"name": "赵六",
		"title": "后端开发",
	})
	r = store.import_file(src)
	assert r.name == "赵六"
	assert r.modules == []
	assert r.job_intention is None


def test_import_missing_optional_fields(tmp_path):
	"""导入缺失可选字段的格式"""
	store = ResumeStore(tmp_path / "resumes")
	src = tmp_path / "input.json"
	_write_json(src, {
		"name": "钱七",
		"title": "测试工程师",
		"modules": [
			{"id": "edu", "title": "教育"},
		],
	})
	r = store.import_file(src)
	assert r.name == "钱七"
	assert r.personal_info.items == []
	assert r.modules[0].icon == ""
	assert r.modules[0].rows == []


def test_import_invalid_json(tmp_path):
	"""导入无效 JSON 报错"""
	store = ResumeStore(tmp_path / "resumes")
	src = tmp_path / "input.json"
	src.write_text("not valid json {{{", encoding="utf-8")
	try:
		store.import_file(src)
		assert False, "Should raise"
	except (json.JSONDecodeError, ValueError):
		pass


def test_import_camelcase_show_background(tmp_path):
	"""camelCase showBackground 正确映射"""
	store = ResumeStore(tmp_path / "resumes")
	src = tmp_path / "input.json"
	_write_json(src, {
		"name": "测试",
		"title": "开发",
		"jobIntentionSection": {
			"title": "求职意向",
			"icon": "mdi:target",
			"items": [{"label": "薪资", "value": "20K"}],
			"showBackground": False,
		},
	})
	r = store.import_file(src)
	assert r.job_intention is not None
	assert r.job_intention.show_background is False
