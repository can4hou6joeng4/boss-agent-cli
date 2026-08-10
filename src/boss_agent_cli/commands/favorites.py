from typing import Any

import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import (
	boss_command_for_ctx,
	handle_auth_errors,
	handle_error_output,
	handle_not_supported,
	handle_output,
	handle_platform_error_output,
	render_simple_list,
)

MAX_FAVORITES_PAGES = 50
FAVORITES_TAG = 4


class FavoritesPageLimitExceeded(RuntimeError):
	"""The remote list still has more data after the bounded page budget."""


def _card_to_shortlist_item(card: dict[str, Any]) -> dict[str, Any]:
	"""geekGetJob card → shortlist record。

	字段名以 mitmproxy 实测为准（securityId/encryptJobId/jobName/brandName/cityName/jobSalary）。
	NOT NULL 字段（title/company/city/salary）兜底空串，防脏 card 触发 IntegrityError。
	"""
	labels = card.get("jobLabels")
	return {
		"security_id": str(card.get("securityId") or ""),
		"job_id": str(card.get("encryptJobId") or ""),
		"title": str(card.get("jobName", "") or ""),
		"company": str(card.get("brandName", "") or ""),
		"city": str(card.get("cityName", "") or ""),
		"salary": str(card.get("jobSalary", "") or ""),
		"source": "favorites",
		"tags": list(labels) if isinstance(labels, list) else [],
		"note": "",
	}


def _redact_for_display(item: dict[str, Any]) -> dict[str, Any]:
	"""脱敏预览输出：security_id/job_id → [REDACTED]（照 export 脱敏约定，issue #354 契约）。

	list 预览不回显完整标识；sync 落库仍存完整值（用户从 shortlist list 取值喂 AI 命令）。
	"""
	redacted = dict(item)
	for key in ("security_id", "job_id"):
		if key in redacted:
			redacted[key] = "[REDACTED]"
	return redacted


def collect_favorites_items(
	platform: Any,
	*,
	max_pages: int = MAX_FAVORITES_PAGES,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
	"""安全聚合多页职位收藏，保留原始顺序。

	返回 (items, error_response)：
	- 成功聚合：([cards...], None)
	- 任一页平台失败：([], raw_response)

	v1 固定 isActive=true（仅有效职位）；encryptJobId 做 seen_signature（比 securityId 稳定，
	避免 securityId 跨请求变化导致重复页检测失效）。
	"""
	items: list[dict[str, Any]] = []
	page = 1
	seen_signatures: set[tuple[str, ...]] = set()

	for _ in range(max_pages):
		resp = platform.job_favorites(page=page, tag=FAVORITES_TAG, is_active=True)
		if not platform.is_success(resp):
			return [], resp

		platform_data = platform.unwrap_data(resp) or {}
		if not isinstance(platform_data, dict):
			return [], resp
		page_items = platform_data.get("cardList") or []
		if not isinstance(page_items, list):
			return [], resp
		if not page_items:
			break

		signature = tuple(str(item.get("encryptJobId", "")) for item in page_items if isinstance(item, dict))
		if signature in seen_signatures:
			break
		seen_signatures.add(signature)
		items.extend(page_items)

		if platform_data.get("hasMore") is False:
			break
		page += 1
	else:
		raise FavoritesPageLimitExceeded(
			f"职位收藏超过安全分页上限 {max_pages} 页，未写入不完整结果"
		)

	return items, None


@click.group("favorites")
def favorites_group() -> None:
	"""读取 BOSS 职位收藏并同步到本地候选池（list 远端只读预览，sync 写入本地 shortlist）。"""


@favorites_group.command("list")
@click.option("--page", default=1, type=click.IntRange(1), show_default=True, help="页码")
@click.pass_context
@handle_auth_errors("favorites-list")
def favorites_list_cmd(ctx: click.Context, page: int) -> None:
	"""预览职位收藏单页（不落库）。

	security_id/job_id 脱敏为 [REDACTED]（遵循导出脱敏约定）；终端表格不显示这两个 ID。
	查看详情或喂给 ai analyze-jd/fit/resume-optimize/chat-coach，需先 favorites sync 落库，
	再用 `boss --json shortlist list` 取完整标识。
	"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_platform_instance(ctx, auth) as platform:
		try:
			raw = platform.job_favorites(page=page, tag=FAVORITES_TAG, is_active=True)
		except NotImplementedError as exc:
			handle_not_supported(ctx, "favorites-list", exc, fallback_message="当前平台不支持职位收藏")
			return
		if not platform.is_success(raw):
			handle_platform_error_output(
				ctx, "favorites-list", platform, raw,
				fallback_message="职位收藏获取失败",
			)
			return
		platform_data = platform.unwrap_data(raw) or {}
		if not isinstance(platform_data, dict):
			handle_platform_error_output(
				ctx, "favorites-list", platform, raw,
				fallback_message="职位收藏响应格式无效",
			)
			return
		cards = platform_data.get("cardList") or []
		if not isinstance(cards, list):
			handle_platform_error_output(
				ctx, "favorites-list", platform, raw,
				fallback_message="职位收藏响应格式无效",
			)
			return

	items = [_redact_for_display(_card_to_shortlist_item(card)) for card in cards if isinstance(card, dict)]

	pagination = {
		"page": page,
		"has_more": platform_data.get("hasMore", False),
		"total": len(items),
	}
	hints = {
		"next_actions": [
			f"使用 {boss_command_for_ctx(ctx, 'favorites sync')} 同步全部收藏到本地候选池（完整字段落库）",
			f"使用 {boss_command_for_ctx(ctx, '--json shortlist list')} 读取完整 security_id 与 job_id（终端表格不显示 ID，需 --json）",
			f"使用 {boss_command_for_ctx(ctx, 'detail <security_id> --job-id <job_id>')} 查看详情（httpx 快速通道只用 job_id，sid 过期不影响取 JD）",
			f"使用 {boss_command_for_ctx(ctx, f'favorites list --page {page + 1}')} 查看下一页",
		],
	}
	handle_output(
		ctx, "favorites-list", items,
		render=lambda data: render_simple_list(
			data, "favorites",
			[
				("title", "title", "bold cyan"),
				("company", "company", "green"),
				("city", "city", "yellow"),
				("salary", "salary", "dim"),
				("source", "source", "magenta"),
			],
		),
		pagination=pagination, hints=hints,
	)


@favorites_group.command("sync")
@click.pass_context
@handle_auth_errors("favorites-sync")
def favorites_sync_cmd(ctx: click.Context) -> None:
	"""同步全部职位收藏到本地候选池（远端只读，本地 upsert 并刷新访问 ID）。"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_platform_instance(ctx, auth) as platform:
		try:
			cards, error_response = collect_favorites_items(platform)
		except NotImplementedError as exc:
			handle_not_supported(ctx, "favorites-sync", exc, fallback_message="当前平台不支持职位收藏")
			return
		except FavoritesPageLimitExceeded as exc:
			handle_error_output(
				ctx,
				"favorites-sync",
				code="RESULT_LIMIT_REACHED",
				message=str(exc),
				recoverable=True,
				recovery_action="缩小远端收藏数量后重试",
			)
			return
		if error_response is not None:
			handle_platform_error_output(
				ctx, "favorites-sync", platform, error_response,
				fallback_message="职位收藏同步失败",
			)
			return

		items = [_card_to_shortlist_item(card) for card in cards if isinstance(card, dict)]

		with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
			result = cache.add_shortlist_batch(items, source="favorites")

	handle_output(
		ctx, "favorites-sync",
		{
			"imported_count": result["imported_count"],
			"existing_count": result["existing_count"],
			"skipped_count": result["skipped_count"],
		},
		hints={
			"next_actions": [
				boss_command_for_ctx(ctx, "shortlist list"),
				boss_command_for_ctx(ctx, "shortlist compare"),
				"使用 boss apply <security_id> <job_id> 投递，或 boss greet <security_id> <job_id> 沟通",
			],
		},
	)
