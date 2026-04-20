import click

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.display import handle_output, render_simple_list


@click.group("shortlist")
def shortlist_group() -> None:
	"""管理职位候选池。"""


@shortlist_group.command("add")
@click.argument("security_id")
@click.argument("job_id")
@click.option("--title", default="", help="职位名称")
@click.option("--company", default="", help="公司名称")
@click.option("--city", default="", help="城市")
@click.option("--salary", default="", help="薪资")
@click.option("--source", default="manual", help="来源，如 search/recommend/show/manual")
@click.pass_context
def shortlist_add_cmd(ctx: click.Context, security_id: str, job_id: str, title: str, company: str, city: str, salary: str, source: str) -> None:
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		cache.add_shortlist(
			{
				"security_id": security_id,
				"job_id": job_id,
				"title": title,
				"company": company,
				"city": city,
				"salary": salary,
				"source": source,
			}
		)
	handle_output(
		ctx,
		"shortlist",
		{
			"action": "add",
			"security_id": security_id,
			"job_id": job_id,
			"title": title,
			"company": company,
			"city": city,
			"salary": salary,
			"source": source,
		},
		hints={"next_actions": ["boss shortlist list", f"boss shortlist remove {security_id} {job_id}"]},
	)


@shortlist_group.command("list")
@click.pass_context
def shortlist_list_cmd(ctx: click.Context) -> None:
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		items = cache.list_shortlist()
	handle_output(
		ctx,
		"shortlist",
		items,
		render=lambda data: render_simple_list(
			data,
			"shortlist",
			[
				("title", "title", "bold cyan"),
				("company", "company", "green"),
				("city", "city", "yellow"),
				("salary", "salary", "dim"),
				("source", "source", "magenta"),
			],
		),
		hints={"next_actions": ["boss detail <security_id> --job-id <job_id>"]},
	)


@shortlist_group.command("remove")
@click.argument("security_id")
@click.argument("job_id")
@click.pass_context
def shortlist_remove_cmd(ctx: click.Context, security_id: str, job_id: str) -> None:
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		removed = cache.remove_shortlist(security_id, job_id)
	handle_output(
		ctx,
		"shortlist",
		{"action": "remove", "security_id": security_id, "job_id": job_id, "removed": removed},
		hints={"next_actions": ["boss shortlist list"]},
	)
