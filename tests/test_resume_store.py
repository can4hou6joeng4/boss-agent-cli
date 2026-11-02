import json
import os

import pytest

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


def test_list_all_skips_unreadable_or_non_object_files(tmp_path):
	"""一个坏文件（非 UTF-8 / 顶层不是对象）不该让整份简历列表都报错。"""
	store = ResumeStore(tmp_path / "resumes")
	store.save(ResumeData(name="张三", title="Python 开发"))
	(store._dir / "gbk.json").write_bytes('{"name": "李四"}'.encode("gbk"))
	(store._dir / "array.json").write_text('["x"]', encoding="utf-8")
	(store._dir / "broken.json").write_text('{"name":', encoding="utf-8")

	items = store.list_all()
	assert [item["name"] for item in items] == ["张三"]


def test_get_returns_none_for_unreadable_resume(tmp_path):
	store = ResumeStore(tmp_path / "resumes")
	(store._dir / "gbk.json").write_bytes('{"name": "李四"}'.encode("gbk"))
	(store._dir / "bad_shape.json").write_text('{"name": "王五", "personal_info": [1]}', encoding="utf-8")

	assert store.get("gbk") is None
	assert store.get("bad_shape") is None


def test_save_failure_keeps_previous_resume(tmp_path, monkeypatch):
	"""保存中断（磁盘满、替换失败）不能把上一版简历写成半截 JSON。"""
	store = ResumeStore(tmp_path / "resumes")
	store.save(ResumeData(name="张三", title="Python 开发"))
	path = store._dir / "张三.json"
	original = path.read_bytes()

	def fail_replace(source, destination):
		raise OSError("disk full")

	monkeypatch.setattr(os, "replace", fail_replace)
	with pytest.raises(OSError):
		store.save(ResumeData(name="张三", title="Go 开发"))

	assert path.read_bytes() == original
	assert store.get("张三") is not None
