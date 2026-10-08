"""Derive the README teaser GIF and site poster from the canonical keynote MP4."""

import subprocess
from pathlib import Path

root = Path(__file__).resolve().parent
showcase = root.parent / "showcase"
film = showcase / "boss-agent-cli-showcase.mp4"
if not film.exists():
	raise SystemExit("Render demo/showcase/boss-agent-cli-showcase.mp4 first")

subprocess.run(
	[
		"ffmpeg",
		"-y",
		"-v",
		"error",
		"-ss",
		"4",
		"-i",
		str(film),
		"-frames:v",
		"1",
		"-q:v",
		"2",
		str(showcase / "boss-agent-cli-showcase-thumb.jpg"),
	],
	check=True,
)
parts = []
for tag, start in zip("abcdef", [3.5, 11.5, 19.5, 27.5, 35.5, 44.5]):
	parts.append(f"[0:v]trim=start={start}:end={start + 3},setpts=PTS-STARTPTS[{tag}]")
parts += [
	"[a][b][c][d][e][f]concat=n=6:v=1:a=0,fps=8,scale=800:-1:flags=lanczos,split[v][p]",
	"[p]palettegen=max_colors=128:stats_mode=diff[pal]",
	"[v][pal]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle[out]",
]
subprocess.run(
	[
		"ffmpeg",
		"-y",
		"-v",
		"error",
		"-i",
		str(film),
		"-filter_complex",
		";".join(parts),
		"-map",
		"[out]",
		"-loop",
		"0",
		str(showcase / "boss-agent-cli-showcase.gif"),
	],
	check=True,
)
print("Exported 18-second, 800px / 8fps teaser and native-resolution poster.")
