# boss-agent-cli 历史宣传片源工程

2026-10-08 前的 31 秒展示动画源：一个基于 **HTML 时间轴**的动效工程，
终端原生暗色风格，1920×1080 / 31s，覆盖四个叙事段落——
搜索 + 福利筛选 · `schema` + JSON 信封 · 合规护栏 · AI 增强 + 多平台。

当前 README 与项目站点的 48 秒发布会动画来自 [../keynote-animation/](../keynote-animation/README.md)。本目录保留作历史参考，文案不代表当前能力；导出历史片请使用临时输出目录。

[English](README.en.md)

## 结构

| 文件 | 职责 |
|------|------|
| `boss-agent-cli-promo.html` | 入口：加载 React + Babel 与下列 JSX |
| `app.jsx` | 组装持久化的终端外壳，按时间窗编排六个镜头 |
| `scenes.jsx` | 六个镜头的内容（sprite-local 时间轴） |
| `lib.jsx` | 设计 token、终端外壳与可复用的时间驱动组件 |
| `animations.jsx` | 时间轴引擎（`Stage` / `Sprite` / 缓动 / 插值） |

## 预览

JSX 经浏览器内 Babel 加载，必须走 HTTP（`file://` 不可用）：

```bash
python3 -m http.server 4311 --directory demo
# 打开 http://localhost:4311/promo-showcase/boss-agent-cli-promo.html
# 空格播放 · ←/→ 逐帧 · 0 复位（播放位置持久化到 localStorage）
```

## 重新导出历史片（使用临时目录）

`<Stage>` 暴露 `window.__animStage` 桥接（`setTime` / `setPlaying` / `duration`），
可被无头浏览器逐帧驱动：

- **MP4**：以 `?capture=1` 加载（去除控件、锁 1:1），逐帧 `setTime()` 截图后用 ffmpeg 编码（1920×1080 / 30fps）。
- **GIF**：由 MP4 经 ffmpeg 两遍调色板降采样生成（900px / 12fps）：

```bash
ffmpeg -i boss-agent-cli-showcase.mp4 -vf "fps=12,scale=900:-1:flags=lanczos,palettegen=stats_mode=diff" palette.png
ffmpeg -i boss-agent-cli-showcase.mp4 -i palette.png \
  -lavfi "fps=12,scale=900:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle" \
  boss-agent-cli-showcase.gif
```

改文案 / 配色 / 镜头节奏：编辑 `scenes.jsx`（内容）或 `lib.jsx`（token 与外壳），重新导出即可。
