"""附件简历消息校验与安全落盘；不执行聊天写操作。"""
from io import BytesIO
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from zipfile import BadZipFile, ZipFile

import httpx

from boss_agent_cli.auth.manager import AuthRequired

MAX_RESUME_BYTES = 20 * 1024 * 1024


class ResumeValidationError(ValueError):
	"""平台数据无法确定目标或附件内容，不应继续操作。"""


def incoming_message(data: Any, friend_id: int, message_id: int) -> dict[str, Any]:
	items = data.get("messages") if isinstance(data, dict) else None
	items = items if isinstance(items, list) else []
	matches = [item for item in items if isinstance(item, dict) and str(item.get("mid")) == str(message_id)]
	if len(matches) != 1:
		raise ResumeValidationError("聊天记录未返回唯一匹配的消息，请先核对 hr chatmsg")
	message = matches[0]
	sender = message.get("from")
	if not isinstance(sender, dict) or str(sender.get("uid")) != str(friend_id) or sender.get("source", 0) != 0:
		raise ResumeValidationError("该消息不是指定候选人发来的消息")
	return message


def resume_friend(data: Any, friend_id: int) -> dict[str, Any]:
	items = data.get("friendList") if isinstance(data, dict) else None
	items = items if isinstance(items, list) else []
	matches = [item for item in items if isinstance(item, dict) and str(item.get("uid", item.get("friendId"))) == str(friend_id) and item.get("friendSource", 0) == 0]
	if len(matches) != 1:
		raise ResumeValidationError("无法确定指定候选人的当前会话")
	return matches[0]


def history_messages(data: Any) -> list[dict[str, Any]]:
	"""取出 chat_history 返回中的消息列表；结构不对时返回空列表。"""
	items = data.get("messages") if isinstance(data, dict) else None
	return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _collect_strings(value: Any, out: list[str], depth: int = 0) -> None:
	if depth > 4:
		return
	if isinstance(value, str):
		out.append(value)
	elif isinstance(value, dict):
		for item in value.values():
			_collect_strings(item, out, depth + 1)
	elif isinstance(value, list):
		for item in value:
			_collect_strings(item, out, depth + 1)


def message_texts(message: dict[str, Any]) -> list[str]:
	"""收集一条消息里可能展示给用户的文案（body 各层文本与 pushText）。"""
	out: list[str] = []
	_collect_strings(message.get("body"), out)
	push_text = message.get("pushText")
	if isinstance(push_text, str):
		out.append(push_text)
	return out


def message_mid(message: dict[str, Any]) -> int | None:
	try:
		return int(str(message.get("mid")))
	except (TypeError, ValueError):
		return None


def message_time_ms(message: dict[str, Any]) -> int | None:
	value = message.get("time")
	if isinstance(value, bool):
		return None
	try:
		return int(str(value))
	except (TypeError, ValueError):
		return None


def new_messages_matching(
	data: Any,
	texts: tuple[str, ...],
	*,
	baseline_mids: set[int] | None,
	since_ms: int,
) -> list[dict[str, Any]]:
	"""找出动作之后新出现、且文案命中 texts 的消息。

	有动作前的消息快照时按 mid 判新旧（不受本机时钟偏差影响）；
	没有快照时退回按消息时间与动作开始时间比较。
	"""
	matches: list[dict[str, Any]] = []
	for message in history_messages(data):
		mid = message_mid(message)
		if baseline_mids is not None:
			if mid is None or mid in baseline_mids:
				continue
		else:
			sent_at = message_time_ms(message)
			if sent_at is None or sent_at < since_ms:
				continue
		if any(text in candidate for candidate in message_texts(message) for text in texts):
			matches.append(message)
	return matches


def attachment_params(message: dict[str, Any]) -> dict[str, str]:
	"""仅读取附件卡片的参数；绝不请求卡片提供的任意 URL。"""
	body = message.get("body")
	link = body.get("hyperLink") if isinstance(body, dict) else None
	if not isinstance(link, dict) or link.get("hyperLinkType") not in (1, 9):
		raise ResumeValidationError("该消息不是已收到的附件简历；请先同意请求并等待附件消息")
	url = link.get("url")
	if not isinstance(url, str):
		raise ResumeValidationError("附件消息缺少链接参数")
	query = parse_qs(urlsplit(url).query, keep_blank_values=True)
	if any(len(values) != 1 for values in query.values()):
		raise ResumeValidationError("附件消息包含重复参数")
	values = {key: value[0] for key, value in query.items()}
	link_type = values.get("type")
	if link_type == "selectResumePreviewUrl":
		resume_id = values.get("encryptId")
	elif link_type == "openFile":
		resume_id = values.get("id")
	else:
		resume_id = values.get("id") or values.get("encryptId")
	if not resume_id:
		raise ResumeValidationError("附件消息缺少简历 ID")
	params = {"id": resume_id}
	if values.get("authType"):
		params["authType"] = values["authType"]
	return params


def save_resume(response: httpx.Response, output: Path) -> dict[str, Any]:
	"""限制大小、识别文件格式，再以私有权限排他创建文件，不覆盖旧文件。"""
	if response.status_code in (401, 403):
		raise AuthRequired("附件下载认证失败，请重新登录后核对附件权限")
	if response.status_code != 200:
		raise ResumeValidationError("附件下载未返回文件（可能已失效、需登录或发生重定向）")
	content = bytearray()
	for chunk in response.iter_bytes(chunk_size=64 * 1024):
		content.extend(chunk)
		if len(content) > MAX_RESUME_BYTES:
			raise ResumeValidationError("附件超过 20 MiB 下载上限")
	suffixes: tuple[str, ...] = ()
	if content.startswith(b"%PDF-"):
		suffixes = (".pdf",)
	elif content.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
		suffixes = (".doc",)
	elif content.startswith(b"\x89PNG\r\n\x1a\n"):
		suffixes = (".png",)
	elif content.startswith(b"\xff\xd8\xff"):
		suffixes = (".jpg", ".jpeg")
	elif content.startswith(b"PK\x03\x04"):
		try:
			with ZipFile(BytesIO(content)) as archive:
				if {"[Content_Types].xml", "word/document.xml"}.issubset(archive.namelist()):
					suffixes = (".docx",)
		except BadZipFile:
			pass
	if not suffixes:
		raise ResumeValidationError("响应不是支持的 PDF、Word 或图片附件，不保存登录页或错误内容")
	if output.suffix.lower() not in suffixes:
		raise ResumeValidationError(f"文件内容与输出扩展名不符，请使用 {suffixes[0]}")
	# 不使用服务端文件名；排他创建避免覆盖，也兼容不支持硬链接的文件系统。
	fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
	try:
		try:
			with open(fd, "wb", closefd=False) as stream:
				stream.write(content)
		finally:
			os.close(fd)
	except BaseException:
		# 关闭句柄后删除本次创建的未完成文件，兼容 Windows。
		output.unlink(missing_ok=True)
		raise
	return {"path": str(output.absolute()), "bytes": len(content), "format": suffixes[0][1:]}
