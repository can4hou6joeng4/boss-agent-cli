#!/usr/bin/env python3
"""生成 Kaku 风格的 CONTRIBUTORS.svg(圆形头像 + 姓名,base64 内嵌,无外部依赖)。

用法:GITHUB_TOKEN 可选(提高限额),python scripts/update_contributors.py
布局参数与 tw93/Kaku 的 update-contributors workflow 保持一致:
svgWidth=1000 avatarSize=72 avatarMargin=45 userNameHeight=20
"""

import base64
import json
import math
import os
import re
import urllib.request
from urllib.parse import urlparse

REPO = "can4hou6joeng4/boss-agent-cli"
EXCLUDE = {"github-actions", "web-flow", "dependabot", "claude"}
SVG_WIDTH, AVATAR, MARGIN, NAME_H = 1000, 72, 45, 20
OUT = os.path.join(os.path.dirname(__file__), "..", "CONTRIBUTORS.svg")
# 头像 CDN 对同一张图会返回不同编码的 PNG(不同节点给的字节不一样)。直接比文件会让每次
# 合并都刷出一个只改头像字节的 PR,合并它又触发下一次。比较时去掉内嵌图片数据,
# 只有贡献者、顺序、名字或布局变了才改写文件。
EMBEDDED_IMAGE = re.compile(r'data:[^;"]+;base64,[A-Za-z0-9+/=]+')


def fetch(url, raw=False):
	req = urllib.request.Request(url, headers={"User-Agent": "contributors-svg"})
	token = os.environ.get("GITHUB_TOKEN")
	if token and urlparse(url).hostname == "api.github.com":
		req.add_header("Authorization", f"Bearer {token}")
	with urllib.request.urlopen(req, timeout=30) as r:
		data = r.read()
		return (data, r.headers.get("Content-Type", "image/png")) if raw else json.loads(data)


def layout(svg):
	"""去掉内嵌头像数据后的 SVG,用来判断是否需要改写。"""
	return EMBEDDED_IMAGE.sub("data:", svg)


def render(users):
	items = []
	cols = (SVG_WIDTH - MARGIN) // (AVATAR + MARGIN)
	for i, u in enumerate(users):
		profile = fetch(u["url"])
		name = profile.get("name") or u["login"]
		img, ctype = fetch(u["avatar_url"] + ("&" if "?" in u["avatar_url"] else "?") + "s=144", raw=True)
		avatar = f"data:{ctype.split(';')[0]};base64," + base64.b64encode(img).decode()
		x = MARGIN + (i % cols) * (AVATAR + MARGIN)
		y = MARGIN // 2 + (i // cols) * (AVATAR + NAME_H + MARGIN // 2)
		items.append(f'''<g transform="translate({x}, {y})">
  <defs>
    <clipPath id="cp-{u["login"]}">
      <circle cx="36" cy="36" r="36" />
    </clipPath>
  </defs>
  <a xlink:href="{u["html_url"]}" href="{u["html_url"]}" class="contributor-link" target="_blank" rel="nofollow sponsored" title="{name}">
    <image width="72" height="72" xlink:href="{avatar}" href="{avatar}" clip-path="url(#cp-{u["login"]})" />
    <text x="36" y="86" text-anchor="middle" alignment-baseline="middle" font-size="10" fill="#666" font-family="-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif">{name}</text>
  </a>
</g>''')

	rows = math.ceil(len(users) / cols)
	height = MARGIN // 2 + rows * (AVATAR + NAME_H + MARGIN // 2)
	svg = (
		f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
		f'width="{SVG_WIDTH}" height="{height}" viewBox="0 0 {SVG_WIDTH} {height}">\n'
		+ "\n".join(items)
		+ "\n</svg>\n"
	)
	return svg, rows


def write_if_changed(svg, path=OUT):
	"""布局有变化才写文件;只有头像字节不同时保留原文件,返回是否写入。"""
	try:
		with open(path, encoding="utf-8") as f:
			current = f.read()
	except FileNotFoundError:
		current = ""
	if layout(current) == layout(svg):
		return False
	with open(path, "w", encoding="utf-8") as f:
		f.write(svg)
	return True


def main():
	users = [
		u
		for u in fetch(f"https://api.github.com/repos/{REPO}/contributors?per_page=100")
		if u["type"] != "Bot" and u["login"].lower() not in EXCLUDE
	]
	svg, rows = render(users)
	state = "updated" if write_if_changed(svg) else "unchanged (avatar bytes only), kept existing file"
	print(f"CONTRIBUTORS.svg: {len(users)} contributors, {rows} rows, {len(svg) // 1024}KB, {state}")


if __name__ == "__main__":
	main()
