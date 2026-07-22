#!/usr/bin/env python3
"""生成 Kaku 风格的 CONTRIBUTORS.svg(圆形头像 + 姓名,base64 内嵌,无外部依赖)。

用法:GITHUB_TOKEN 可选(提高限额),python scripts/update_contributors.py
布局参数与 tw93/Kaku 的 update-contributors workflow 保持一致:
svgWidth=1000 avatarSize=72 avatarMargin=45 userNameHeight=20
"""
import base64, json, math, os, urllib.request
from urllib.parse import urlparse

REPO = "can4hou6joeng4/boss-agent-cli"
EXCLUDE = {"github-actions", "web-flow", "dependabot", "claude"}
SVG_WIDTH, AVATAR, MARGIN, NAME_H = 1000, 72, 45, 20
OUT = os.path.join(os.path.dirname(__file__), "..", "CONTRIBUTORS.svg")

def fetch(url, raw=False):
    req = urllib.request.Request(url, headers={"User-Agent": "contributors-svg"})
    token = os.environ.get("GITHUB_TOKEN")
    if token and urlparse(url).hostname == "api.github.com":
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
        return (data, r.headers.get("Content-Type", "image/png")) if raw else json.loads(data)

users = [u for u in fetch(f"https://api.github.com/repos/{REPO}/contributors?per_page=100")
         if u["type"] != "Bot" and u["login"].lower() not in EXCLUDE]

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
svg = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
       f'width="{SVG_WIDTH}" height="{height}" viewBox="0 0 {SVG_WIDTH} {height}">\n'
       + "\n".join(items) + "\n</svg>\n")
with open(OUT, "w") as f:
    f.write(svg)
print(f"CONTRIBUTORS.svg: {len(users)} contributors, {rows} rows, {len(svg)//1024}KB")
