"""Verify full-resolution determinism, rendered motion, browser controls and media."""

import base64
import functools
import hashlib
import http.server
import io
import json
import socketserver
import subprocess
import threading
from pathlib import Path

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parent
out = root / "qa"
out.mkdir(exist_ok=True)


class Quiet(http.server.SimpleHTTPRequestHandler):
	def log_message(self, *args):
		pass


server = socketserver.TCPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(root)))
threading.Thread(target=server.serve_forever, daemon=True).start()
report = {"errors": [], "full_resolution_determinism": [], "interval_motion": []}
url = f"http://127.0.0.1:{server.server_address[1]}/index.html"
grab = "t => {renderFrame(t); return __canvas.toDataURL('image/png').split(',')[1];}"


def pixels(encoded):
	return np.asarray(Image.open(io.BytesIO(base64.b64decode(encoded))).convert("RGB")).astype(np.int16)


with sync_playwright() as p:
	browser = p.chromium.launch()
	page = browser.new_page(viewport={"width": 1440, "height": 1080})
	page.on("pageerror", lambda e: report["errors"].append(str(e)))
	page.goto(url)
	page.wait_for_function("window.__ready || window.__bootFailed")
	if page.evaluate("window.__bootFailed || null"):
		raise RuntimeError(page.evaluate("window.__bootFailed"))
	for chapter, scene in enumerate(["hero", "search", "roles", "agent", "control", "outro"]):
		t = chapter * 8 + 4
		a = page.evaluate(grab, t)
		page.evaluate(grab, 47 - t / 2)
		b = page.evaluate(grab, t)
		equal = a == b
		report["full_resolution_determinism"].append(
			{
				"scene": scene,
				"time": t,
				"png_bytes_equal": equal,
				"sha256": hashlib.sha256(base64.b64decode(a)).hexdigest(),
			}
		)
		assert equal, scene
		f0 = pixels(page.evaluate(grab, chapter * 8 + 5))
		f1 = pixels(page.evaluate(grab, chapter * 8 + 6))
		delta = np.abs(f1 - f0).max(axis=2)
		report["interval_motion"].append(
			{
				"scene": scene,
				"times": [chapter * 8 + 5, chapter * 8 + 6],
				"changed_over_4_pct": round(float((delta > 4).mean() * 100), 3),
				"changed_over_12_pct": round(float((delta > 12).mean() * 100), 3),
			}
		)
		assert (delta > 4).any(), scene

	page.locator('[data-scene="4"]').click()
	assert page.locator("#tt").inner_text().startswith("32.0")
	page.locator("#play").click()
	page.wait_for_function("parseFloat(document.getElementById('tt').textContent) > 32.4")
	page.locator("#play").click()
	t1 = float(page.locator("#scrub").input_value())
	page.keyboard.press("ArrowRight")
	t2 = float(page.locator("#scrub").input_value())
	assert abs(t2 - t1 - 1 / 30) < 0.001
	page.keyboard.press("Home")
	assert float(page.locator("#scrub").input_value()) == 0
	page.locator("#sound").click()
	page.locator("#play").click()
	page.wait_for_function("document.getElementById('audio').currentTime > 0.25")
	assert page.evaluate("!document.getElementById('audio').muted && !document.getElementById('audio').paused")
	page.locator("#play").click()
	report["controls"] = {
		"chapter_seek": True,
		"play_pause": True,
		"frame_step": True,
		"home_reset": True,
		"music_toggle_and_playback": True,
	}
	page.set_viewport_size({"width": 390, "height": 844})
	assert page.evaluate("document.documentElement.scrollWidth <= 390"), "mobile horizontal overflow"
	report["mobile_no_horizontal_overflow"] = True
	page.screenshot(path=str(out / "preview-mobile.png"), full_page=True)
	page.set_viewport_size({"width": 1440, "height": 1080})
	page.locator('[data-scene="0"]').click()
	page.evaluate(
		"document.getElementById('scrub').value=4; document.getElementById('scrub').dispatchEvent(new Event('input'))"
	)
	page.screenshot(path=str(out / "preview-desktop.png"), full_page=True)
	browser.close()
server.shutdown()

film = root.parent / "showcase/boss-agent-cli-showcase.mp4"
if not film.exists():
	film = root / "output/boss-agent-cli-keynote.mp4"
probe = subprocess.run(
	[
		"ffprobe",
		"-v",
		"error",
		"-show_entries",
		"format=duration,size:stream=codec_name,codec_type,width,height,r_frame_rate,nb_frames,duration,sample_rate,channels",
		"-of",
		"json",
		str(film),
	],
	check=True,
	text=True,
	capture_output=True,
)
report["media"] = json.loads(probe.stdout)
assert float(report["media"]["format"]["duration"]) == 48
v, a = report["media"]["streams"]
assert (v["width"], v["height"], v["r_frame_rate"], v["nb_frames"]) == (1920, 1080, "30/1", "1440")
assert a["channels"] == 2 and float(a["duration"]) == 48
subprocess.run(["ffmpeg", "-v", "error", "-i", str(film), "-f", "null", "-"], check=True)
report["complete_decode"] = True
(out / "verification.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
assert not report["errors"], report["errors"]
print(
	json.dumps(
		{
			k: report[k]
			for k in [
				"full_resolution_determinism",
				"interval_motion",
				"controls",
				"mobile_no_horizontal_overflow",
				"complete_decode",
			]
		},
		ensure_ascii=False,
		indent=2,
	)
)
