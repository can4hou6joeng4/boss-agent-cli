import json
from datetime import datetime
from pathlib import Path

from boss_agent_cli.resume.models import ResumeData, ResumeFile, dict_to_resume, resume_to_dict


def _safe_filename(name: str) -> str:
	"""将简历名称转为安全的文件名"""
	return name.replace("/", "_").replace("\\", "_").replace("\0", "_")


class ResumeStore:
	"""简历文件存储（JSON 文件），存储路径 ~/.boss-agent/resumes/"""

	def __init__(self, resumes_dir: Path):
		self._dir = resumes_dir
		self._dir.mkdir(parents=True, exist_ok=True)

	def _path_for(self, name: str) -> Path:
		return self._dir / f"{_safe_filename(name)}.json"

	def list_all(self) -> list[dict]:
		"""列出所有简历摘要（name, title, updated_at）"""
		results: list[dict] = []
		for path in sorted(self._dir.glob("*.json")):
			try:
				raw = json.loads(path.read_text(encoding="utf-8"))
				results.append({
					"name": raw.get("name", ""),
					"title": raw.get("title", ""),
					"updated_at": raw.get("updated_at", ""),
				})
			except (json.JSONDecodeError, OSError):
				continue
		return results

	def get(self, name: str) -> ResumeData | None:
		"""按名称读取简历"""
		path = self._path_for(name)
		if not path.exists():
			return None
		try:
			raw = json.loads(path.read_text(encoding="utf-8"))
			return dict_to_resume(raw)
		except (json.JSONDecodeError, OSError):
			return None

	def save(self, resume: ResumeData) -> None:
		"""保存/更新简历，自动更新 updated_at"""
		resume.updated_at = datetime.now().isoformat()
		d = resume_to_dict(resume)
		path = self._path_for(resume.name)
		path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

	def delete(self, name: str) -> bool:
		"""删除简历，返回是否成功"""
		path = self._path_for(name)
		if not path.exists():
			return False
		path.unlink()
		return True

	def exists(self, name: str) -> bool:
		"""检查简历是否存在"""
		return self._path_for(name).exists()

	def import_file(self, file_path: Path) -> ResumeData:
		"""导入 JSON 文件（兼容 wzdnzd/zine0 camelCase 格式）"""
		raw_text = file_path.read_text(encoding="utf-8")
		raw = json.loads(raw_text)

		if "version" in raw and "data" in raw and isinstance(raw["data"], dict):
			raw = raw["data"]

		resume = dict_to_resume(raw)
		self.save(resume)
		return resume

	def export_json(self, name: str) -> str:
		"""导出为 ResumeFile JSON 字符串"""
		resume = self.get(name)
		if resume is None:
			raise FileNotFoundError(f"简历 '{name}' 不存在")

		from boss_agent_cli import __version__

		envelope = ResumeFile(data=resume)
		envelope.metadata["exported_at"] = datetime.now().isoformat()
		envelope.metadata["app_version"] = __version__

		d = {
			"version": envelope.version,
			"data": resume_to_dict(resume),
			"metadata": envelope.metadata,
		}
		return json.dumps(d, ensure_ascii=False, indent=2)

	def clone(self, name: str, new_name: str) -> ResumeData:
		"""复制简历为新版本"""
		resume = self.get(name)
		if resume is None:
			raise FileNotFoundError(f"简历 '{name}' 不存在")
		resume.name = new_name
		resume.created_at = ""
		resume.updated_at = ""
		resume.__post_init__()
		self.save(resume)
		return resume
