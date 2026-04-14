import json

from boss_agent_cli.resume.models import ResumeData, PersonalInfoSection, PersonalInfoItem, ResumeModule
from boss_agent_cli.resume.store import ResumeStore


def test_save_and_get(tmp_path):
	store = ResumeStore(tmp_path / "resumes")
	r = ResumeData(name="张三", title="Python 开发")
	store.save(r)
	loaded = store.get("张三")
	assert loaded is not None
	assert loaded.name == "张三"
	assert loaded.title == "Python 开发"


def test_get_nonexistent(tmp_path):
	store = ResumeStore(tmp_path / "resumes")
	assert store.get("不存在") is None


def test_save_updates_timestamp(tmp_path):
	store = ResumeStore(tmp_path / "resumes")
	r = ResumeData(name="张三", title="开发", updated_at="2026-01-01T00:00:00")
	store.save(r)
	loaded = store.get("张三")
	assert loaded is not None
	assert loaded.updated_at != "2026-01-01T00:00:00"


def test_list_all(tmp_path):
	store = ResumeStore(tmp_path / "resumes")
	store.save(ResumeData(name="张三", title="Python 开发"))
	store.save(ResumeData(name="李四", title="Go 开发"))
	store.save(ResumeData(name="王五", title="前端工程师"))
	items = store.list_all()
	assert len(items) == 3
	names = [item["name"] for item in items]
	assert "张三" in names
	assert "李四" in names
	assert "王五" in names
	for item in items:
		assert "name" in item
		assert "title" in item
		assert "updated_at" in item


def test_delete_success(tmp_path):
	store = ResumeStore(tmp_path / "resumes")
	store.save(ResumeData(name="张三", title="开发"))
	assert store.delete("张三") is True
	assert store.get("张三") is None


def test_delete_nonexistent(tmp_path):
	store = ResumeStore(tmp_path / "resumes")
	assert store.delete("不存在") is False


def test_exists(tmp_path):
	store = ResumeStore(tmp_path / "resumes")
	assert store.exists("张三") is False
	store.save(ResumeData(name="张三", title="开发"))
	assert store.exists("张三") is True


def test_export_json(tmp_path):
	store = ResumeStore(tmp_path / "resumes")
	r = ResumeData(
		name="张三",
		title="Python 开发",
		personal_info=PersonalInfoSection(
			items=[PersonalInfoItem(label="邮箱", value="test@example.com")],
		),
		modules=[ResumeModule(id="skills", title="技能", rows=[{"type": "tags", "tags": ["Python"]}])],
	)
	store.save(r)
	exported = store.export_json("张三")
	data = json.loads(exported)
	assert data["version"] == "1.0"
	assert data["data"]["name"] == "张三"
	assert data["metadata"]["source"] == "boss-agent-cli"


def test_export_json_nonexistent(tmp_path):
	store = ResumeStore(tmp_path / "resumes")
	try:
		store.export_json("不存在")
		assert False, "Should raise"
	except FileNotFoundError:
		pass


def test_clone(tmp_path):
	store = ResumeStore(tmp_path / "resumes")
	store.save(ResumeData(name="张三", title="Python 开发"))
	cloned = store.clone("张三", "张三-v2")
	assert cloned.name == "张三-v2"
	assert cloned.title == "Python 开发"
	assert store.exists("张三")
	assert store.exists("张三-v2")


def test_clone_nonexistent(tmp_path):
	store = ResumeStore(tmp_path / "resumes")
	try:
		store.clone("不存在", "新版本")
		assert False, "Should raise"
	except FileNotFoundError:
		pass


def test_save_overwrite(tmp_path):
	store = ResumeStore(tmp_path / "resumes")
	store.save(ResumeData(name="张三", title="Python 开发"))
	store.save(ResumeData(name="张三", title="Go 开发"))
	loaded = store.get("张三")
	assert loaded is not None
	assert loaded.title == "Go 开发"
	assert len(store.list_all()) == 1


def test_save_preserves_full_data(tmp_path):
	store = ResumeStore(tmp_path / "resumes")
	r = ResumeData(
		name="张三",
		title="全栈",
		center_title=True,
		personal_info=PersonalInfoSection(
			items=[
				PersonalInfoItem(label="手机", value="13800138000"),
				PersonalInfoItem(label="邮箱", value="z@example.com", icon="mdi:email"),
			],
			layout="grid",
		),
		modules=[
			ResumeModule(id="work", title="工作经历", icon="mdi:briefcase", rows=[
				{"type": "richtext", "columns": 2, "content": ["A 公司", "2020-2026"]},
			]),
		],
		avatar="https://example.com/avatar.jpg",
	)
	store.save(r)
	loaded = store.get("张三")
	assert loaded is not None
	assert loaded.center_title is True
	assert loaded.personal_info.layout == "grid"
	assert len(loaded.personal_info.items) == 2
	assert loaded.personal_info.items[1].icon == "mdi:email"
	assert loaded.modules[0].icon == "mdi:briefcase"
	assert loaded.avatar == "https://example.com/avatar.jpg"
