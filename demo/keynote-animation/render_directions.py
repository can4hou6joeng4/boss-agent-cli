"""Render the same hero at t=4 in three themes, and a labeled comparison sheet."""

import base64
import functools
import http.server
import socketserver
import threading
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parent
out = root / "directions"
out.mkdir(exist_ok=True)


class Quiet(http.server.SimpleHTTPRequestHandler):
	def log_message(self, *args):
		pass


server = socketserver.TCPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(root)))
threading.Thread(target=server.serve_forever, daemon=True).start()
with sync_playwright() as p:
	browser = p.chromium.launch()
	page = browser.new_page(viewport={"width": 1920, "height": 1400}, device_scale_factor=1)
	page.goto(f"http://127.0.0.1:{server.server_address[1]}/directions.html")
	page.wait_for_function("window.__ready || window.__bootFailed")
	if page.evaluate("window.__bootFailed || null"):
		raise RuntimeError(page.evaluate("window.__bootFailed"))
	for theme in ["A", "B", "C"]:
		encoded = page.evaluate("id => document.getElementById(id).toDataURL('image/png').split(',')[1]", theme)
		(out / f"{theme}.png").write_bytes(base64.b64decode(encoded))
	browser.close()
server.shutdown()
sheet = Image.new("RGB", (1600, 1110), "#14191e")
draw = ImageDraw.Draw(sheet)
font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 22)
for i, (key, title) in enumerate(
	[("A", "A / AURORA KEYNOTE"), ("B", "B / DAYLIGHT STUDIO"), ("C", "C / TERMINAL SIGNAL")]
):
	x, y = (28, 28) if i == 0 else (812, 28) if i == 1 else (420, 570)
	draw.text((x, y), title, font=font, fill="#eff3f5")
	img = Image.open(out / f"{key}.png").convert("RGB").resize((760, 428), Image.Resampling.LANCZOS)
	sheet.paste(img, (x, y + 46))
sheet.save(out / "three-directions.jpg", quality=94)
print(out / "three-directions.jpg")
