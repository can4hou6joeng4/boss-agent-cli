# boss-agent-cli historical promotional film source

This HTML timeline project generated the 31-second showcase used before 2026-10-08. It uses a terminal-style dark scene at 1920 × 1080, covering search and welfare filtering, schema and JSON, historical guardrails, and AI / platform concepts.

The current README and project site's 48-second keynote comes from [../keynote-animation/](../keynote-animation/README.en.md). This directory is retained as historical reference; its copy does not describe current capabilities. Render historical previews to a temporary output directory.

[中文](README.md)

## Structure

| File | Purpose |
|---|---|
| `boss-agent-cli-promo.html` | Loads React, Babel, and the JSX modules |
| `app.jsx` | Assembles the persistent terminal and six scenes |
| `scenes.jsx` | Scene content and local timelines |
| `lib.jsx` | Design tokens, terminal frame, and shared components |
| `animations.jsx` | Timeline engine and interpolation |

## Preview and export

Serve over HTTP because browser Babel cannot load the JSX through `file://`:

```bash
python3 -m http.server 4311 --directory demo
# Open http://localhost:4311/promo-showcase/boss-agent-cli-promo.html
```

Space plays; left/right step frames; 0 resets. Playback position is saved in localStorage.

The `window.__animStage` bridge exposes `setTime`, `setPlaying`, and `duration`. Capture through `?capture=1` to hide controls and lock scale, drive the bridge frame by frame, and encode at 1920 × 1080 / 30 fps. Convert a temporary MP4 to GIF with ffmpeg's `palettegen` / `paletteuse` filters; keep the temporary output separate from the current `demo/showcase/` assets.

Edit `scenes.jsx` for copy and pacing, or `lib.jsx` for colors and frame styling.
