"""Minimal BOSS recruiter MQTT transport for marking one chat as read."""

from __future__ import annotations

import random
import struct
import threading
import time
from dataclasses import dataclass
from typing import Any

import paho.mqtt.client as mqtt


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
	timeout: float = 15.0,
) -> dict[str, Any]:
	"""Connect once, publish presence and one read receipt, then disconnect."""
	if peer_uid <= 0 or message_id <= 0:
		raise ValueError("peer_uid and message_id must be positive integers")

	connected = threading.Event()
	client_id = f"ws-{random.getrandbits(64):016x}"
	client = mqtt.Client(
		mqtt.CallbackAPIVersion.VERSION2,
		client_id=client_id,
		protocol=mqtt.MQTTv31,
		transport="websockets",
	)
	client.username_pw_set(credentials.username, credentials.password)
	client.tls_set()
	cookie_header = "; ".join(f"{key}={value}" for key, value in cookies.items())
	client.ws_set_options(
		path="/chatws",
		headers={"Origin": "https://www.zhipin.com", "Cookie": cookie_header, "User-Agent": user_agent},
	)

	def on_connect(client_obj: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
		if int(reason_code) == 0:
			client_obj.subscribe("chat", qos=1)
		connected.reason_code = 0  # type: ignore[attr-defined]
		if int(reason_code) != 0:
			connected.reason_code = int(reason_code)  # type: ignore[attr-defined]
		connected.set()

	client.on_connect = on_connect
	client.connect(credentials.server, port=443, keepalive=25)
	client.loop_start()
	try:
		if not connected.wait(timeout):
			raise RecruiterMqttError("mqtt connect timeout")
		reason_code = getattr(connected, "reason_code", 0)
		if reason_code:
			raise RecruiterMqttError(f"mqtt connection rejected: {reason_code}")
		presence = encode_presence(
			user_id=credentials.user_id,
			uniqid=credentials.uniqid,
			client_ip=credentials.client_ip,
		)
		client.publish("chat", presence, qos=1, retain=True).wait_for_publish(timeout=timeout)
		time.sleep(0.7)
		read_time = int(time.time() * 1000)
		payload = encode_message_read(
			user_id=peer_uid,
			message_id=message_id,
			user_source=user_source,
			read_time_ms=read_time,
		)
		info = client.publish("chat", payload, qos=1, retain=True)
		info.wait_for_publish(timeout=timeout)
		if not info.is_published():
			raise RecruiterMqttError("messageRead publish was not acknowledged")
		return {
			"ok": True,
			"peer_uid": peer_uid,
			"message_id": message_id,
			"user_source": user_source,
			"read_time": read_time,
		}
	finally:
		client.disconnect()
		client.loop_stop()
