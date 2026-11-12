"""boss stats — 投递转化漏斗统计。

只读聚合本地缓存数据，给出打招呼 → 投递 → 候选池 → 监控新增的全景视图。
支持 JSON 信封（默认）和自包含 HTML 交互式报表两种输出格式。
"""

from __future__ import annotations

import html
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import click
from rich.table import Table

from boss_agent_cli import display
from boss_agent_cli.cache.store import CacheDatabaseInvalid
from boss_agent_cli.display import boss_command_for_ctx, handle_output, json_forced, render_action_result, shell_argument
from boss_agent_cli.services.output_path import prepare_output_path


def _safe_count(conn: sqlite3.Connection, table: str) -> int:
	try:
		result: int = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
		return result
	except sqlite3.OperationalError:
		return 0


def _count_since(conn: sqlite3.Connection, table: str, column: str, since: float) -> int:
	try:
		result: int = conn.execute(
			f"SELECT COUNT(*) FROM {table} WHERE {column} >= ?", (since,)
		).fetchone()[0]
		return result
	except sqlite3.OperationalError:
		return 0


def _ratio(numer: int, denom: int) -> float:
	if denom <= 0:
		return 0.0
	return round(numer / denom, 4)


def _collect_stats(db_path: Path, days: int) -> dict[str, Any]:
	"""从 SQLite 收集漏斗数据，返回结构化 dict。"""
	if not db_path.exists():
		return {
			"funnel": {"greeted": 0, "applied": 0, "shortlist": 0, "watch_hits": 0},
			"conversion": {"apply_rate": 0.0, "shortlist_rate": 0.0},
			"window_days": days,
			"note": "缓存尚未建立，先跑一次 search/greet/apply 再查看",
		}

	since = time.time() - days * 86400
	conn = sqlite3.connect(str(db_path))
	try:
		try:
			greeted_total = _safe_count(conn, "greet_records")
			applied_total = _safe_count(conn, "apply_records")
			shortlist_total = _safe_count(conn, "shortlist_records")

			greeted_window = _count_since(conn, "greet_records", "greeted_at", since)
			applied_window = _count_since(conn, "apply_records", "applied_at", since)
			shortlist_window = _count_since(conn, "shortlist_records", "created_at", since)
			watch_window = _count_since(conn, "watch_hits", "first_seen_at", since)
		except sqlite3.DatabaseError as exc:
			# 只读聚合直接开 SQLite，不经过 CacheStore 的启动校验；库文件损坏时
			# 交给 main.py 统一出 CACHE_DATABASE_INVALID 信封，而不是 traceback。
			raise CacheDatabaseInvalid(db_path, str(exc)) from exc
	finally:
		conn.close()

	return {
		"window_days": days,
		"funnel": {
			"greeted": greeted_total,
			"applied": applied_total,
			"shortlist": shortlist_total,
		},
		"window": {
			"greeted": greeted_window,
			"applied": applied_window,
			"shortlist": shortlist_window,
			"watch_hits": watch_window,
		},
		"conversion": {
			"apply_rate": _ratio(applied_total, greeted_total),
			"shortlist_rate": _ratio(shortlist_total, greeted_total),
			"apply_rate_window": _ratio(applied_window, greeted_window),
		},
	}


def _hint_command(ctx: click.Context, command: str) -> str:
	"""漏斗统计的后继提示沿用本地缓存与配置所在的数据目录。"""
	return boss_command_for_ctx(ctx, command, include_data_dir=True)


def _build_hints(ctx: click.Context, data: dict[str, Any]) -> list[str]:
	hints: list[str] = []
	funnel = data.get("funnel", {})
	greeted_total = funnel.get("greeted", 0)
	applied_total = funnel.get("applied", 0)
	if greeted_total == 0:
		hints.append(_hint_command(ctx, "search <query>"))
	elif applied_total == 0 and greeted_total > 0:
		hints.append(_hint_command(ctx, "apply <security_id> <job_id>"))
	hints.append(_hint_command(ctx, "pipeline"))
	hints.append(_hint_command(ctx, "follow-up"))
	return hints


def _build_operator_actions(ctx: click.Context, data: dict[str, Any]) -> list[str]:
	"""转化率建议属于操作者判断，放 operator_actions，不能混进可执行命令。"""
	funnel = data.get("funnel", {})
	apply_rate = data.get("conversion", {}).get("apply_rate", 0)
	if apply_rate < 0.1 and funnel.get("greeted", 0) >= 20:
		return [f"打招呼转投递率偏低，考虑调整目标岗位或优化简历（{_hint_command(ctx, 'ai optimize')}）"]
	return []


def _render_html(ctx: click.Context, data: dict[str, Any]) -> str:
	"""渲染自包含交互式 HTML 报表（纯 CSS + SVG，无外部依赖）。"""
	funnel = data.get("funnel", {})
	window = data.get("window", {})
	conversion = data.get("conversion", {})

	greeted = int(funnel.get("greeted", 0))
	applied = int(funnel.get("applied", 0))
	shortlist = int(funnel.get("shortlist", 0))

	# 漏斗各层宽度（按最大值归一化到 100-400px）
	max_count = max(greeted, applied, shortlist, 1)

	def _bar_width(count: int) -> int:
		if max_count == 0:
			return 100
		return 100 + int(300 * count / max_count)

	apply_rate_pct = round(conversion.get("apply_rate", 0) * 100, 2)
	shortlist_rate_pct = round(conversion.get("shortlist_rate", 0) * 100, 2)
	apply_rate_window_pct = round(conversion.get("apply_rate_window", 0) * 100, 2)

	window_days = int(data.get("window_days", 30))
	note = html.escape(str(data.get("note", "")))
	generated_at = time.strftime("%Y-%m-%d %H:%M:%S")

	return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>boss-agent-cli 投递漏斗报表</title>
<style>
:root {{
	--bg: #0f172a;
	--card: #1e293b;
	--text: #e2e8f0;
	--muted: #94a3b8;
	--accent: #3b82f6;
	--success: #10b981;
	--warning: #f59e0b;
	--danger: #ef4444;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
	font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
	background: var(--bg);
	color: var(--text);
	line-height: 1.6;
	padding: 32px 24px;
	max-width: 960px;
	margin: 0 auto;
}}
header {{ margin-bottom: 32px; }}
h1 {{ font-size: 28px; margin-bottom: 8px; }}
.meta {{ color: var(--muted); font-size: 14px; }}
.grid {{
	display: grid;
	grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
	gap: 16px;
	margin-bottom: 32px;
}}
.card {{
	background: var(--card);
	border-radius: 12px;
	padding: 20px;
	border: 1px solid rgba(148,163,184,0.1);
}}
.card .label {{ color: var(--muted); font-size: 13px; margin-bottom: 8px; }}
.card .value {{ font-size: 32px; font-weight: 700; }}
.card .sub {{ color: var(--muted); font-size: 12px; margin-top: 4px; }}
.card.accent .value {{ color: var(--accent); }}
.card.success .value {{ color: var(--success); }}
.card.warning .value {{ color: var(--warning); }}
section {{ margin-bottom: 32px; }}
section h2 {{ font-size: 18px; margin-bottom: 16px; color: var(--text); }}
.funnel {{ padding: 16px 0; }}
.funnel-row {{ display: flex; align-items: center; margin-bottom: 12px; gap: 16px; }}
.funnel-label {{ width: 100px; color: var(--muted); font-size: 14px; flex-shrink: 0; }}
.funnel-bar {{
	height: 44px;
	background: linear-gradient(90deg, var(--accent), #6366f1);
	border-radius: 6px;
	display: flex;
	align-items: center;
	justify-content: flex-end;
	padding-right: 12px;
	color: white;
	font-weight: 600;
	font-size: 15px;
	transition: width 0.4s ease;
}}
.funnel-bar.applied {{ background: linear-gradient(90deg, var(--success), #059669); }}
.funnel-bar.shortlist {{ background: linear-gradient(90deg, var(--warning), #d97706); }}
.hints {{ background: var(--card); border-radius: 12px; padding: 20px; }}
.hints ul {{ list-style: none; }}
.hints li {{
	padding: 8px 0;
	border-bottom: 1px solid rgba(148,163,184,0.1);
	font-family: "SF Mono", Menlo, Consolas, monospace;
	font-size: 13px;
	color: var(--accent);
}}
.hints li:last-child {{ border: 0; }}
footer {{ color: var(--muted); font-size: 12px; text-align: center; padding-top: 24px; border-top: 1px solid rgba(148,163,184,0.1); }}
footer a {{ color: var(--accent); text-decoration: none; }}
.note {{
	background: rgba(245,158,11,0.1);
	color: var(--warning);
	padding: 12px 16px;
	border-radius: 8px;
	margin-bottom: 24px;
	border-left: 3px solid var(--warning);
}}
</style>
</head>
<body>

<header>
	<h1>📊 投递转化漏斗</h1>
	<div class="meta">窗口期：最近 {window_days} 天 · 生成时间：{generated_at}</div>
</header>

{f'<div class="note">⚠️ {note}</div>' if note else ''}

<section>
	<h2>核心指标</h2>
	<div class="grid">
		<div class="card accent">
			<div class="label">打招呼总数</div>
			<div class="value">{greeted}</div>
			<div class="sub">累计 · 窗口 {window.get("greeted", 0)}</div>
		</div>
		<div class="card success">
			<div class="label">投递总数</div>
			<div class="value">{applied}</div>
			<div class="sub">累计 · 窗口 {window.get("applied", 0)}</div>
		</div>
		<div class="card warning">
			<div class="label">候选池</div>
			<div class="value">{shortlist}</div>
			<div class="sub">累计 · 窗口 {window.get("shortlist", 0)}</div>
		</div>
		<div class="card">
			<div class="label">投递转化率</div>
			<div class="value">{apply_rate_pct}%</div>
			<div class="sub">窗口 {apply_rate_window_pct}%</div>
		</div>
	</div>
</section>

<section>
	<h2>漏斗图</h2>
	<div class="funnel">
		<div class="funnel-row">
			<span class="funnel-label">打招呼</span>
			<div class="funnel-bar" style="width: {_bar_width(greeted)}px;">{greeted}</div>
		</div>
		<div class="funnel-row">
			<span class="funnel-label">投递</span>
			<div class="funnel-bar applied" style="width: {_bar_width(applied)}px;">{applied} ({apply_rate_pct}%)</div>
		</div>
		<div class="funnel-row">
			<span class="funnel-label">候选池</span>
			<div class="funnel-bar shortlist" style="width: {_bar_width(shortlist)}px;">{shortlist} ({shortlist_rate_pct}%)</div>
		</div>
	</div>
</section>

<section>
	<h2>下一步建议</h2>
	<div class="hints">
		<ul>
{chr(10).join(f'			<li>{html.escape(h)}</li>' for h in _build_hints(ctx, data))}
		</ul>
	</div>
</section>

<footer>
	Generated by <a href="https://github.com/can4hou6joeng4/boss-agent-cli">boss-agent-cli</a> · stats 命令
</footer>

</body>
</html>
"""


def _render_stats(ctx: click.Context, data: dict[str, Any]) -> None:
	"""把漏斗统计渲染成终端表格（TTY 路径）。

	兼容 ``_collect_stats`` 的两种形状：有缓存时含 ``window`` 段；
	无缓存时 ``watch_hits`` 落在 ``funnel`` 内并带 ``note``。
	"""
	days = data.get("window_days", 30)
	funnel = data.get("funnel", {})
	window = data.get("window", {})
	conversion = data.get("conversion", {})

	table = Table(title=f"求职漏斗 · 最近 {days} 天")
	table.add_column("阶段", style="bold cyan")
	table.add_column("总计", justify="right", style="green")
	table.add_column(f"近 {days} 天", justify="right", style="yellow")

	for label, key in (("打招呼", "greeted"), ("投递", "applied"), ("候选池", "shortlist"), ("监控命中", "watch_hits")):
		total = funnel.get(key)
		recent = window.get(key)
		table.add_row(label, "-" if total is None else str(total), "-" if recent is None else str(recent))
	display.console.print(table)

	display.console.print(f"  [dim]打招呼 → 投递[/dim]    {_percent(conversion.get('apply_rate'))}")
	display.console.print(f"  [dim]打招呼 → 候选池[/dim]  {_percent(conversion.get('shortlist_rate'))}")

	if note := data.get("note"):
		display.console.print(f"\n  [yellow]{note}[/yellow]")

	if hints := _build_hints(ctx, data):
		display.console.print("\n  [bold]下一步：[/bold]")
		for hint in hints:
			# 目录名是外部输入：含 Rich 标签时必须按字面渲染，否则会被吞或抛 MarkupError。
			display.console.print(f"    [dim]{display.escape(hint)}[/dim]")


def _percent(value: Any) -> str:
	"""把 0~1 的比率渲染成百分比；非数值返回占位符。"""
	try:
		return f"{round(float(value) * 100, 1)}%"
	except (TypeError, ValueError):
		return "-"


@click.command("stats")
# 上限 100 年：`since = time.time() - days * 86400` 要把天数转成 float，
# 无界的整数（如 10**400）会抛 OverflowError，命令连信封都发不出来。
@click.option("--days", default=30, type=click.IntRange(min=0, max=36500), help="统计窗口天数（默认 30 天）")
@click.option(
	"--format", "output_format",
	type=click.Choice(["json", "html"]),
	default="json",
	help="输出格式（json 走 JSON 信封；html 生成自包含报表）",
)
@click.option(
	"-o", "--output", "output_path",
	type=click.Path(dir_okay=False, writable=True, path_type=Path),
	default=None,
	help="HTML 输出路径（仅 --format html 时有效，未指定时写到 stdout；--json 时放进信封 data.content）",
)
@click.pass_context
def stats_cmd(ctx: click.Context, days: int, output_format: str, output_path: Path | None) -> None:
	"""投递转化漏斗统计（只读聚合）"""
	data_dir: Path = ctx.obj["data_dir"]
	db_path = data_dir / "cache" / "boss_agent.db"

	data = _collect_stats(db_path, days)

	if output_format == "html":
		html_text = _render_html(ctx, data)
		if output_path:
			target = prepare_output_path(output_path)
			target.write_text(html_text, encoding="utf-8")
			handle_output(
				ctx, "stats",
				{"format": "html", "path": str(target), "bytes": len(html_text.encode("utf-8"))},
				render=lambda d: render_action_result(d, title="stats（HTML 报表）", next_steps=[f"open {shell_argument(str(target))}"]),
				hints={"next_actions": [f"open {shell_argument(str(target))}"]},
			)
		elif json_forced(ctx):
			# 同 digest：`--json` 下 stdout 必须是信封，把 HTML 放进 data.content。
			handle_output(
				ctx, "stats",
				{"format": "html", "content": html_text, "bytes": len(html_text.encode("utf-8"))},
				hints={"next_actions": [_hint_command(ctx, "stats --format html -o <path>")],
				"operator_actions": ["把 data.content 写成 .html 文件后打开"]},
			)
		else:
			# stdout 直出 HTML（便于管道重定向）
			sys.stdout.write(html_text)
			sys.stdout.flush()
		return

	stats_hints: dict[str, Any] = {"next_actions": _build_hints(ctx, data)}
	stats_operator_actions = _build_operator_actions(ctx, data)
	if stats_operator_actions:
		stats_hints["operator_actions"] = stats_operator_actions
	handle_output(ctx, "stats", data, render=lambda d: _render_stats(ctx, d), hints=stats_hints)
