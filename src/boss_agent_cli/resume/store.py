import errno
import json
import stat
from datetime import datetime
from pathlib import Path
from typing import Any

from boss_agent_cli.resume.models import ResumeData, ResumeFile, dict_to_resume, resume_to_dict
from boss_agent_cli.services.json_files import path_mode, write_json_atomic


class ResumeImportError(ValueError):
	"""导入文件不是可用的简历 JSON；消息是给用户看的修复指引。"""


class ResumeAlreadyExistsError(ValueError):
	"""导入时遇到同名简历，且没有显式要求覆盖。"""

	def __init__(self, name: str):
		super().__init__(name)
		self.name = name


_WINDOWS_RESERVED_DEVICE_NAMES = frozenset(
	{"CON", "PRN", "AUX", "NUL"}
	| {f"COM{index}" for index in range(1, 10)}
	| {f"LPT{index}" for index in range(1, 10)}
)
#: Windows 文件名不允许的字符（除路径分隔符、NUL 与 ':' 外的通配符 / 重定向符），
#: 以及所有控制字符。它们不能出现在 `save()` 生成的 `<name>.json` 里。
_WINDOWS_ILLEGAL_FILENAME_CHARACTERS = frozenset('*?|<>"')


def _is_windows_device_name(name: str) -> bool:
	"""名称是否解析到 Windows 保留设备（CON/NUL/COM1…，含带扩展名形式）。

	Windows 对设备名不区分大小写，``CON.txt`` 与 ``CON`` 一样解析到设备；
	Win32 还会先去掉名称结尾的空格再比较。
	"""
	stem = name.split(".", 1)[0].rstrip(" \t").upper()
	return stem in _WINDOWS_RESERVED_DEVICE_NAMES


def _is_windows_illegal_filename_char(char: str) -> bool:
	"""Windows 文件名非法字符：通配符 / 重定向符与所有控制字符。"""
	return char in _WINDOWS_ILLEGAL_FILENAME_CHARACTERS or ord(char) < 32 or ord(char) == 127


def _safe_filename(name: str) -> str:
	"""将简历名称转为安全的文件名。

	除路径分隔符与 NUL 外，还要处理 Windows 上会改变路径语义的三种名称：
	``:`` 会被解释成盘符（``D:evil.json`` 与 ``ntpath.join(r"C:\\base", ...)``
	拼接后脱离 base）、CON/NUL/COM1 等保留设备名不会落成普通文件、纯点号名称
	（``.`` / ``..``）拼接后指向目录本身或父目录。
	"""
	safe = name.replace("/", "_").replace("\\", "_").replace("\0", "_").replace(":", "_")
	if safe and not safe.strip("."):
		# "." / ".." 拼成 "<name>.json" 后仍会指回当前目录或父目录。
		safe = safe.replace(".", "_")
	if _is_windows_device_name(safe):
		safe = f"_{safe}"
	return safe


def _portable_filename(name: str) -> str:
	"""在 ``_safe_filename`` 之上再替换 Windows 无法落盘的字符（Issue #1768）。

	``*?|<>"`` 与控制字符会让 Windows 的 ``open()`` / ``mkstemp()`` 直接失败。
	新建的简历与默认导出名用这份文件名；旧版本在 macOS / Linux 上按原字符写出
	的文件由 ``ResumeStore._path_for`` 继续按旧文件名找回。
	"""
	return "".join("_" if _is_windows_illegal_filename_char(char) else char for char in _safe_filename(name))


def _unsafe_resume_name_reason(name: str) -> str | None:
	"""名称不能安全落盘时返回给用户看的原因（Issue #1507）。

	导入的 JSON 来自外部文件：名称里带盘符 / 路径分隔符、Windows 保留设备名或
	只有点号时直接拒绝，而不是静默改名后让用户按原名称找不到简历。``resume
	init`` / ``clone`` 的合法名称仍由 ``_safe_filename`` 统一清洗，读写两侧共用
	同一份规则。
	"""
	if any(char in name for char in ("/", "\\", "\0", ":")):
		return "name 不能包含路径分隔符、NUL 或 ':'（Windows 盘符）"
	if not name.strip("."):
		return "name 不能只由点号组成"
	if _is_windows_device_name(name):
		return f"name '{name}' 是 Windows 保留设备名，请换一个名称"
	return None


def _path_mode(path: Path) -> int | None:
	"""仅对缺失路径返回 None；悬空链接保留链接自身的类型（Issue #1692）。"""
	# 3.14 的 Path.exists() / is_file() 会把权限等 OSError 吞掉并返回 False，
	# 同名覆盖保护会因此把「读不到」当成「不存在」而继续写入。
	try:
		mode = path.lstat().st_mode
	except FileNotFoundError:
		return None
	if stat.S_ISLNK(mode):
		try:
			return path.stat().st_mode
		except FileNotFoundError:
			# 悬空链接仍是目录项：按存在处理，删除时只 unlink 链接本身。
			pass
	return mode


class ResumeStore:
	"""简历文件存储（JSON 文件），存储路径 ~/.boss-agent/resumes/"""

	def __init__(self, resumes_dir: Path):
		self._dir = resumes_dir
		self._dir.mkdir(parents=True, exist_ok=True)

	def _path_for(self, name: str) -> Path:
		portable = self._dir / f"{_portable_filename(name)}.json"
		legacy = self._dir / f"{_safe_filename(name)}.json"
		if legacy == portable:
			return portable
		# 旧版本按原字符落盘的简历仍要能读、改、删；状态读不出来时也交给旧路径，
		# 由调用方按既有口径报告，不能悄悄改去写另一个文件。
		try:
			legacy.lstat()
		except FileNotFoundError:
			return portable
		except OSError:
			return legacy
		return legacy

	def list_all(self) -> list[dict[str, Any]]:
		"""列出所有简历摘要（name, title, updated_at）"""
		return self.list_all_with_corrupt()[0]

	def list_all_with_corrupt(self) -> tuple[list[dict[str, Any]], list[str]]:
		"""列出可读的简历摘要，并单独返回读不出来的文件名标记。

		列表命令不能因为一份手写坏的文件整体失败，也不能让坏文件假装不存在：
		调用方据此提示修复或删除重建（`resume init` 遇到同名文件会报
		``RESUME_ALREADY_EXISTS``，用户否则无从得知该删哪一份）。

		可读性判定与 ``get()`` 共用同一套模型校验：字段类型漂移（``name`` 写成
		对象、``rows`` 元素写成字符串）的文件在这里同样进损坏名单，不能出现
		「列表里正常、show/export 报损坏」的矛盾（Issue #1189）。
		"""
		results: list[dict[str, Any]] = []
		corrupt: list[str] = []
		for path in sorted(self._dir.glob("*.json")):
			raw, _ = self._read_payload(path)
			if raw is None:
				corrupt.append(path.stem)
				continue
			try:
				dict_to_resume(raw)
			except (AttributeError, TypeError, ValueError):
				corrupt.append(path.stem)
				continue
			results.append({
				"name": raw.get("name", ""),
				"title": raw.get("title", ""),
				"updated_at": raw.get("updated_at", ""),
			})
		return results, corrupt

	@staticmethod
	def _read_payload(path: Path) -> tuple[dict[str, Any] | None, str | None]:
		"""读取一份简历的原始 JSON；返回（payload, 读取错误说明）。

		文件不存在返回 ``(None, None)``；语法/编码/权限问题与顶层不是对象都
		返回错误说明，交给调用方报 ``RESUME_FILE_INVALID``。
		"""
		try:
			mode = _path_mode(path)
		except (OSError, ValueError) as exc:
			# 路径状态读不出来与「文件不存在」不同：列表要把这份标成损坏，
			# show/export 要报 RESUME_FILE_INVALID，不能误报 not found。
			return None, f"路径状态读取失败：{exc}"
		if mode is None:
			return None, None
		if not stat.S_ISREG(mode):
			# 目录 / FIFO / 悬空链接不能直接 read_text：FIFO 会阻塞。
			return None, "简历路径不是普通文件"
		try:
			raw = json.loads(path.read_text(encoding="utf-8"))
		# ValueError 覆盖 JSONDecodeError / UnicodeDecodeError 与 3.11+ 的超长整数
		# 上限；RecursionError 覆盖深嵌套。读不出来就是文件损坏，报原因而不是冒泡。
		except (ValueError, OSError, RecursionError) as exc:
			return None, f"JSON 读取失败：{exc}"
		if not isinstance(raw, dict):
			return None, "顶层必须是 JSON 对象"
		return raw, None

	def get_with_error(self, name: str) -> tuple[ResumeData | None, str | None]:
		"""按名称读取简历，并带上读不出来的原因（Issue #1189）。

		``(None, None)`` 表示文件不存在；``(None, 原因)`` 表示文件存在但损坏，
		命令层据此区分 ``RESUME_NOT_FOUND`` 与 ``RESUME_FILE_INVALID``。
		"""
		raw, error = self._read_payload(self._path_for(name))
		if raw is None:
			return None, error
		try:
			return dict_to_resume(raw), None
		except (AttributeError, TypeError, ValueError) as exc:
			return None, str(exc) or type(exc).__name__

	def get(self, name: str) -> ResumeData | None:
		"""按名称读取简历"""
		return self.get_with_error(name)[0]

	def save(self, resume: ResumeData) -> None:
		"""保存/更新简历，自动更新 updated_at"""
		resume.updated_at = datetime.now().isoformat()
		d = resume_to_dict(resume)
		# 原子写：编辑到一半被中断不能把用户唯一一份简历写成半截 JSON。
		write_json_atomic(self._path_for(resume.name), d)

	def stored_name(self, name: str) -> str:
		"""按 ``name`` 取到的那份文件里记录的真实名称；读不出来时返回 ``name``。

		``a/b`` 与 ``a_b`` 清洗后落在同一个文件上：按别名取到的简历仍叫 ``a/b``，
		编辑与清理关联都要用这个名字（Issue #1804）。文件里的名称指向另一个
		文件时（手工复制出来的副本）不采信，仍按请求名处理。
		"""
		resume = self.get(name)
		if resume is None or not isinstance(resume.name, str) or not resume.name:
			return name
		if self._path_for(resume.name) != self._path_for(name):
			return name
		return resume.name

	def is_directory(self, name: str) -> bool:
		"""简历路径本身是目录（不跟随链接）：``unlink`` 必然失败（Issue #1805）。"""
		try:
			return stat.S_ISDIR(self._path_for(name).lstat().st_mode)
		except FileNotFoundError:
			return False

	def delete(self, name: str) -> bool:
		"""删除简历，返回是否成功；悬空链接只删除链接本身。"""
		path = self._path_for(name)
		if not self.exists(name):
			return False
		path.unlink()
		return True

	def exists(self, name: str) -> bool:
		"""检查简历是否存在。

		路径状态无法读取时上抛 ``OSError``：同名覆盖保护必须拒绝写入，
		不能把「读不到」当成「不存在」（Issue #1692）。
		"""
		return _path_mode(self._path_for(name)) is not None

	def is_corrupt(self, name: str) -> bool:
		"""文件存在但读不出来（JSON 语法损坏、字段类型写错）。"""
		return self.exists(name) and self.get(name) is None

	def import_file(self, file_path: Path, *, overwrite: bool = False) -> ResumeData:
		"""导入 JSON 文件（兼容 wzdnzd/zine0 camelCase 格式）。

		文件里的 `name` 与已有简历同名时默认抛 `ResumeAlreadyExistsError`：
		`save()` 是整份覆盖，导入一份旧备份会静默丢掉本地已有的编辑（`resume init` /
		`resume clone` 都拒绝同名）。只有调用方显式要求覆盖时才继续。
		"""
		mode = path_mode(file_path)
		# 直接 read_text 会阻塞在 FIFO 上；目录 / 设备 / 悬空链接也没有可读文本。
		if mode is None:
			raise FileNotFoundError(errno.ENOENT, "简历文件不存在", str(file_path))
		if not stat.S_ISREG(mode):
			raise ResumeImportError("路径不是普通文件；请选择 UTF-8 编码的简历 JSON 文件")
		try:
			raw_text = file_path.read_text(encoding="utf-8")
		except UnicodeDecodeError as exc:
			# GBK 保存的 JSON、误选的 PDF / 图片都会走到这里：报一句能指导操作的话，
			# 而不是把 codecs 的 traceback 甩给调用方。
			raise ResumeImportError("文件不是 UTF-8 编码的文本；请先转存为 UTF-8 的 JSON 再导入") from exc
		try:
			raw = json.loads(raw_text)
		except ValueError as exc:
			# 只有 JSONDecodeError 带行列信息；超长整数（3.11+）与编码问题没有。
			position = (
				f"第 {exc.lineno} 行第 {exc.colno} 列：" if isinstance(exc, json.JSONDecodeError) else ""
			)
			raise ResumeImportError(f"不是合法 JSON（{position}{exc}）") from exc
		except RecursionError as exc:
			raise ResumeImportError("不是合法 JSON（嵌套层级过深）") from exc

		if isinstance(raw, dict) and "version" in raw and "data" in raw and isinstance(raw["data"], dict):
			raw = raw["data"]
		if not isinstance(raw, dict):
			raise ResumeImportError("顶层必须是 JSON 对象")
		name = raw.get("name")
		if not isinstance(name, str) or not name.strip():
			raise ResumeImportError("缺少非空字符串字段 name")
		unsafe_reason = _unsafe_resume_name_reason(name)
		if unsafe_reason is not None:
			raise ResumeImportError(f"{unsafe_reason}；请修改文件里的 name 字段后重试")
		if not overwrite and self.exists(name):
			raise ResumeAlreadyExistsError(name)

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
		# 与 stdout 信封同口径：rows 里的 NaN / Infinity 不能让导出文件变成非严格
		# JSON（Issue #1435）。json_safe_value 归一后 allow_nan=False 兜底。
		from boss_agent_cli.output import json_safe_value

		return json.dumps(json_safe_value(d), ensure_ascii=False, allow_nan=False, indent=2)

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
