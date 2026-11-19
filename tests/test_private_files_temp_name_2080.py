"""私有原子写与 crawl 导出的临时名不占用目标文件名长度（Issue #2080）。"""

from __future__ import annotations

import os

import pytest

from boss_agent_cli.crawler.exporter import _temporary_for
from boss_agent_cli.services.private_files import write_private_bytes

_NAME_MAX = 255


def _long_name(tmp_path) -> str:
	name = "a" * _NAME_MAX
	assert len(name) == _NAME_MAX
	return name


def test_write_private_bytes_accepts_name_at_name_max(tmp_path) -> None:
	target = tmp_path / _long_name(tmp_path)

	write_private_bytes(target, b"payload")

	assert target.read_bytes() == b"payload"
	assert (target.stat().st_mode & 0o777) == 0o600
	assert [entry.name for entry in tmp_path.iterdir()] == [target.name]


def test_crawl_temporary_name_does_not_embed_target_name(tmp_path) -> None:
	target = tmp_path / _long_name(tmp_path)

	temporary = _temporary_for(target)
	try:
		assert temporary.exists()
		assert len(temporary.name) < _NAME_MAX
		assert target.name not in temporary.name
	finally:
		temporary.unlink(missing_ok=True)


@pytest.mark.skipif(not hasattr(os, "link"), reason="需要硬链接支持")
def test_salt_creation_accepts_name_at_name_max(tmp_path) -> None:
	from boss_agent_cli.services.private_files import _create_salt

	salt_path = tmp_path / _long_name(tmp_path)

	raw = _create_salt(salt_path)

	assert raw == salt_path.read_bytes()
	assert len(raw) == 16
