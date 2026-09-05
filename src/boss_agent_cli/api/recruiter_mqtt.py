"""Minimal BOSS recruiter MQTT transport for marking one chat as read."""

from __future__ import annotations

import random
import re
import struct
import time
from dataclasses import dataclass
from typing import Any

from boss_agent_cli.api.httpx_helpers import remaining_timeout


class RecruiterMqttError(RuntimeError):
	"""Raised when the chat websocket cannot confirm a publish."""


@dataclass(frozen=True)
class RecruiterMqttCredentials:
	server: str
	username: str
	password: str
	user_id: int
	uniqid: str
	client_ip: str = ""


def _varint(value: int) -> bytes:
	if value < 0:
		raise ValueError("protobuf varint cannot encode a negative value")
	out = bytearray()
	while value > 0x7F:
		out.append((value & 0x7F) | 0x80)
		value >>= 7
	out.append(value)
	return bytes(out)


def _field_varint(number: int, value: int) -> bytes:
	return _varint(number << 3) + _varint(value)


def _field_bytes(number: int, value: bytes) -> bytes:
	return _varint((number << 3) | 2) + _varint(len(value)) + value


def _field_text(number: int, value: str) -> bytes:
	return _field_bytes(number, value.encode("utf-8"))


def encode_presence(*, user_id: int, uniqid: str, client_ip: str = "") -> bytes:
	client_info = b"".join([
		_field_text(1, "4.92"),
		_field_text(5, uniqid),
		_field_text(6, client_ip),
		_field_varint(7, 9018),
		_field_text(8, "web"),
		_field_text(9, "-1"),
		_varint((12 << 3) | 1) + struct.pack("<d", 0.0),
		_varint((13 << 3) | 1) + struct.pack("<d", 0.0),
	])
	presence = b"".join([
		_field_varint(1, 1),
		_field_varint(2, user_id),
		_field_bytes(3, client_info),
		_field_varint(5, 0),
	])
	return _field_varint(1, 2) + _field_bytes(4, presence)


def encode_message_read(*, user_id: int, message_id: int, user_source: int = 0, read_time_ms: int | None = None) -> bytes:
	read_time = read_time_ms if read_time_ms is not None else int(time.time() * 1000)
	message_read = b"".join([
		_field_varint(1, user_id),
		_field_varint(2, message_id),
		_field_varint(3, read_time),
		_field_varint(5, user_source),
	])
	return _field_varint(1, 6) + _field_bytes(8, message_read)


def mark_chat_read(
	credentials: RecruiterMqttCredentials,
	*,
	cookies: dict[str, Any],
	user_agent: str,
	peer_uid: int,
	message_id: int,
	user_source: int = 0,
	deadline: float | None = None,
) -> dict[str, Any]:
	"""单连接发送回执并确认传输；不重连、不把 PUBACK 当作平台已读。"""
	try:
		import paho.mqtt.client as mqtt
	except ImportError as exc:
		raise RecruiterMqttError("MQTT 依赖不可用，请重新安装 boss-agent-cli") from exc

	if deadline is None:
		deadline = time.monotonic() + 25
	if peer_uid <= 0 or message_id <= 0 or user_source < 0:
		raise ValueError("invalid read receipt identifiers")
	host = credentials.server.lower()
	if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+zhipin\.com", host):
		raise RecruiterMqttError("MQTT host must be a zhipin.com subdomain")

	connected = False
	rejected = False
	client = mqtt.Client(
		mqtt.CallbackAPIVersion.VERSION2,
		client_id=f"ws-{random.getrandbits(64):016x}",
		protocol=mqtt.MQTTv31,
		transport="websockets",
		reconnect_on_failure=False,
	)
	client.username_pw_set(credentials.username, credentials.password)
	client.tls_set()
	# 只发送登录 Cookie，wt2 使用本次 bootstrap 得到的新值。
	handshake_cookies = {"wt2": credentials.password}
	if cookies.get("zp_at"):
		handshake_cookies["zp_at"] = str(cookies["zp_at"])
	client.ws_set_options(
		path="/chatws",
		headers={
			"Origin": "https://www.zhipin.com",
			"Cookie": "; ".join(f"{key}={value}" for key, value in handshake_cookies.items()),
			"User-Agent": user_agent,
		},
	)

	def on_connect(client_obj: mqtt.Client, userdata: Any, flags: Any, reason_code: mqtt.ReasonCode, properties: Any) -> None:
		nonlocal connected, rejected
		connected = True
		rejected = reason_code.is_failure

	def poll() -> None:
		code = client.loop(timeout=min(1.0, remaining_timeout(deadline)))
		if code != mqtt.MQTT_ERR_SUCCESS:
			raise RecruiterMqttError("MQTT connection lost")
		remaining_timeout(deadline)

	def publish(payload: bytes) -> None:
		remaining_timeout(deadline)
		info = client.publish("chat", payload, qos=1, retain=True)
		if info.rc != mqtt.MQTT_ERR_SUCCESS:
			raise RecruiterMqttError("MQTT publish rejected")
		while not info.is_published():
			poll()
		remaining_timeout(deadline)

	client.on_connect = on_connect
	client.connect_timeout = remaining_timeout(deadline)
	try:
		# paho 的 WebSocket 握手使用 keepalive 作为 socket timeout。
		client.connect(host, port=443, keepalive=max(1, min(25, int(remaining_timeout(deadline)))))
		while not connected:
			poll()
		if rejected:
			raise RecruiterMqttError("MQTT connection rejected")
		publish(encode_presence(user_id=credentials.user_id, uniqid=credentials.uniqid, client_ip=credentials.client_ip))
		read_time = int(time.time() * 1000)
		publish(encode_message_read(user_id=peer_uid, message_id=message_id, user_source=user_source, read_time_ms=read_time))
		return {"published": True, "peer_uid": peer_uid, "message_id": message_id, "read_time": read_time}
	finally:
		client.disconnect()
