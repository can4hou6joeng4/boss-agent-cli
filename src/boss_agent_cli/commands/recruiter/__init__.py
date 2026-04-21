"""招聘者命令组。"""
import click


@click.group("recruiter")
@click.pass_context
def recruiter_group(ctx: click.Context) -> None:
	"""招聘者模式命令组（需 --role recruiter 启用）"""
