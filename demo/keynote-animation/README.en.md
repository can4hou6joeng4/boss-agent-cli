# boss-agent-cli product keynote

**One command. A connection to opportunity.** A 48-second, 1920 × 1080, 30 fps release film in the selected A “Aurora Keynote” direction. It uses the project's existing PNG logo and an original 120 BPM electronic score.

Content is grounded in the local v3.0.0 code and schema checked on 2026-10-08: BOSS Zhipin is the sole registered platform; 39 top-level commands, 13 recruiter subcommands, and 77 MCP tools. Jobs, salaries, and workflow states are explicitly illustrative. Rendering makes no platform calls and accesses no login state.

[中文](README.md)

## Watch

- Film: [../showcase/boss-agent-cli-showcase.mp4](../showcase/boss-agent-cli-showcase.mp4), shared by the repository README and project site.
- The README GIF is an 18-second teaser with three seconds from each scene (800px / 8fps). The MP4 is the full 48-second film.
- Interactive preview: [index.html](index.html), with spacebar playback, arrow-key frame stepping, chapter navigation, and a music toggle.
- Three visual directions: [directions.html](directions.html).
- Storyboard and copy: [storyboard.json](storyboard.json).

Serve the preview over local HTTP:

```bash
python3 -m http.server 4318 --bind 127.0.0.1 --directory demo
# Open http://127.0.0.1:4318/keynote-animation/
```

## Six scenes

| Time | Theme | Motion |
|---|---|---|
| 00:00–00:08 | One command, opportunity | Logo and orbital paths emerge; terminal prompt lands |
| 00:08–00:16 | Welfare filtering | A real command is typed; two criteria are checked; a nonmatching job retreats |
| 00:16–00:24 | Two roles | Seeker and recruiter workflows unfold with moving signals |
| 00:24–00:32 | Agent integration | Four entry points converge; 77 MCP tools count up once |
| 00:32–00:40 | Recoverable control | Progress reaches a checkpoint, stops, and explicitly resumes |
| 00:40–00:48 | Install and closing | Logo, install command, and repository URL remain readable |

Platform risk codes or security pages stop a workflow and save a checkpoint. The user explicitly resumes after the risk clears. The animation only illustrates that process.

## Reproduce

Requires uv, ffmpeg, and Playwright Chromium. Rendering dependencies remain separate from CLI runtime dependencies.

```bash
uv run --no-project --with playwright playwright install chromium
uv run --no-project --with numpy python demo/keynote-animation/score.py
uv run --no-project --with playwright --with pillow python demo/keynote-animation/render_directions.py
uv run --no-project --with playwright python demo/keynote-animation/render.py \
  --fps 30 --crf 17 --audio demo/keynote-animation/output/soundtrack.m4a \
  --out demo/showcase/boss-agent-cli-showcase.mp4
python3 demo/keynote-animation/export_showcase.py
```

Add `--offline` once dependencies are cached to avoid package-index network failures.

`visuals.js` defines the palette, logo, typography, panels, and background; `scenes.js` implements scene motion; `eras.js` defines the timeline; `player.js` exposes `renderFrame(t)`, `renderSolo(id, lt)`, and `__ready` for huashu-art-motion rendering and QA. Visual output depends only on time. The soundtrack noise uses a fixed random seed.

## Verification and assets

```bash
uv run --no-project --with playwright --with pillow --with numpy python demo/keynote-animation/verify.py
```

`verify.py` writes local full-decode, native-resolution determinism, interval-motion, and player checks to `qa/`. Local rendering and review reports are not committed. The initial independent review inspected 96 half-second samples and five transition sequences; its rejection-reason readability suggestion was fixed and rechecked. Font licensing: [assets/LICENSES.md](assets/LICENSES.md) and [assets/OFL.txt](assets/OFL.txt). The original logo comes from `docs/assets/logo.png`; `score.py` synthesizes the music without external audio samples.

The MP4, GIF, and poster in `demo/showcase/` are the public showcase assets. This project is their current source; `demo/promo-showcase/` is retained as historical source. This directory's `.gitignore` excludes local QA reports, direction exports, WAV files, and duplicate MP4 files from the site.
