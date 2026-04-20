"""Public API 契约测试 — 守护 `boss_agent_cli` 包级导出面不被意外破坏。

这份测试保证下游项目可以通过 `from boss_agent_cli import X` 访问核心类型和异常，
任何改名 / 删除都会在这里被立即捕获。
"""
import importlib

import boss_agent_cli


# ── __all__ 契约 ─────────────────────────────────────────


EXPECTED_EXPORTS = {
	"__version__",
	"AuthManager",
	"AuthRequired",
	"TokenRefreshFailed",
	"BossClient",
	"AuthError",
	"AccountRiskError",
	"JobItem",
	"JobDetail",
	"CacheStore",
	"AIService",
	"AIServiceError",
	"ResumeData",
	"ResumeFile",
	"Platform",
	"BossPlatform",
	"get_platform",
	"list_platforms",
}


def test_all_is_defined():
	assert hasattr(boss_agent_cli, "__all__")
	assert isinstance(boss_agent_cli.__all__, list)


def test_all_matches_expected_exports():
	assert set(boss_agent_cli.__all__) == EXPECTED_EXPORTS


def test_every_export_is_actually_importable():
	"""__all__ 里声明的每个名字都能真的从包里 import 到。"""
	for name in boss_agent_cli.__all__:
		assert hasattr(boss_agent_cli, name), f"{name} declared in __all__ but not exported"


# ── 关键类型的身份一致性 ──────────────────────────────────


def test_auth_manager_identity():
	from boss_agent_cli.auth.manager import AuthManager as OriginalAuthManager
	assert boss_agent_cli.AuthManager is OriginalAuthManager


def test_boss_client_identity():
	from boss_agent_cli.api.client import BossClient as OriginalBossClient
	assert boss_agent_cli.BossClient is OriginalBossClient


def test_cache_store_identity():
	from boss_agent_cli.cache.store import CacheStore as OriginalCacheStore
	assert boss_agent_cli.CacheStore is OriginalCacheStore


def test_job_item_identity():
	from boss_agent_cli.api.models import JobItem as OriginalJobItem
	assert boss_agent_cli.JobItem is OriginalJobItem


def test_ai_service_identity():
	from boss_agent_cli.ai.service import AIService as OriginalAIService
	assert boss_agent_cli.AIService is OriginalAIService


# ── 异常继承关系 ──────────────────────────────────────────


def test_auth_required_is_exception():
	assert issubclass(boss_agent_cli.AuthRequired, Exception)


def test_token_refresh_failed_is_exception():
	assert issubclass(boss_agent_cli.TokenRefreshFailed, Exception)


def test_auth_error_is_exception():
	assert issubclass(boss_agent_cli.AuthError, Exception)


def test_account_risk_error_is_exception():
	assert issubclass(boss_agent_cli.AccountRiskError, Exception)


def test_ai_service_error_is_exception():
	assert issubclass(boss_agent_cli.AIServiceError, Exception)


# ── 平台抽象 ──────────────────────────────────────────────


def test_platform_is_abstract():
	import abc
	assert inspect_abstract(boss_agent_cli.Platform)


def inspect_abstract(cls: type) -> bool:
	import abc
	return bool(getattr(cls, "__abstractmethods__", set()))


def test_boss_platform_subclasses_platform():
	assert issubclass(boss_agent_cli.BossPlatform, boss_agent_cli.Platform)


def test_get_platform_returns_boss_by_default():
	assert boss_agent_cli.get_platform("zhipin") is boss_agent_cli.BossPlatform


def test_list_platforms_contains_zhipin():
	assert "zhipin" in boss_agent_cli.list_platforms()


# ── 版本格式 ──────────────────────────────────────────────


def test_version_is_semver_string():
	version = boss_agent_cli.__version__
	assert isinstance(version, str)
	parts = version.split(".")
	assert len(parts) == 3, f"版本应为 X.Y.Z 格式，实际为 {version}"
	for part in parts:
		assert part.isdigit(), f"版本各段应为数字，实际为 {part}"


# ── py.typed marker ──────────────────────────────────────


def test_py_typed_marker_exists():
	"""PEP 561 标记文件必须存在，下游才能启用类型检查。"""
	pkg_spec = importlib.util.find_spec("boss_agent_cli")
	assert pkg_spec is not None
	assert pkg_spec.origin is not None
	import pathlib
	pkg_dir = pathlib.Path(pkg_spec.origin).parent
	assert (pkg_dir / "py.typed").exists(), "py.typed 标记文件丢失"


# ── 包级 docstring ────────────────────────────────────────


def test_package_has_docstring():
	"""提供给 help(boss_agent_cli) 的入口说明。"""
	assert boss_agent_cli.__doc__ is not None
	assert len(boss_agent_cli.__doc__.strip()) > 0
