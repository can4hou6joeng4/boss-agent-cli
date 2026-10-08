# boss-agent-cli 发布会动画

**一句命令，连接机会。** 48 秒、1920 × 1080、30 fps，采用选定的 A「极光发布会」方向，沿用项目原有 PNG 标志，搭配原创 120 BPM 电子配乐。

内容依据 2026-10-08 本地 v3.0.0 代码与 schema：当前唯一注册平台为 BOSS 直聘；39 个顶层命令、13 个招聘者子命令、77 个 MCP 工具。职位、薪资与流程画面均为明确标注的示意，不调用真实平台或登录态。

[English](README.en.md)

## 观看

- 成片：[../showcase/boss-agent-cli-showcase.mp4](../showcase/boss-agent-cli-showcase.mp4)。该文件同时用于仓库 README 和项目站点。
- README 动图是六个镜头各取 3 秒的 18 秒预览（800px / 8fps），完整片长以 MP4 为准。
- 可交互预览：[index.html](index.html)，支持空格播放、左右逐帧、章节跳转及音乐开关。
- 三方向对比：[directions.html](directions.html)。
- 分镜与文案：[storyboard.json](storyboard.json)。

预览须通过本地 HTTP 服务加载：

```bash
python3 -m http.server 4318 --bind 127.0.0.1 --directory demo
# 打开 http://127.0.0.1:4318/keynote-animation/
```

## 六个镜头

| 时间 | 主题 | 画面动作 |
|---|---|---|
| 00:00–00:08 | 一句命令，连接机会 | 标志与轨道浮现，终端入口落位 |
| 00:08–00:16 | 福利筛选 | 输入真实命令，检查两个条件，排除不匹配职位 |
| 00:16–00:24 | 双角色工作流 | 求职者与招聘者链路展开、信号流动 |
| 00:24–00:32 | Agent 接入 | 四入口汇聚，77 个 MCP 工具计数落定 |
| 00:32–00:40 | 可恢复控制 | 进度到达断点，停止，再显式恢复 |
| 00:40–00:48 | 安装落版 | 标志、安装命令与仓库地址清晰停留 |

命中平台风险码或安全页时停止并保存 checkpoint，风险解除后由用户显式恢复。动画中的恢复操作仅作流程示意。

## 重现

需要 uv、ffmpeg 和 Playwright Chromium。动画依赖仅由渲染命令按需提供，不加入 CLI 运行依赖。

```bash
uv run --no-project --with playwright playwright install chromium
uv run --no-project --with numpy python demo/keynote-animation/score.py
uv run --no-project --with playwright --with pillow python demo/keynote-animation/render_directions.py
uv run --no-project --with playwright python demo/keynote-animation/render.py \
  --fps 30 --crf 17 --audio demo/keynote-animation/output/soundtrack.m4a \
  --out demo/showcase/boss-agent-cli-showcase.mp4
python3 demo/keynote-animation/export_showcase.py
```

依赖已缓存时可添加 `--offline`，避免重现过程受包索引网络影响。

`visuals.js` 定义色板、标志、文字、卡片与背景；`scenes.js` 负责镜头动作；`eras.js` 定义时间轴；`player.js` 暴露 `renderFrame(t)` / `renderSolo(id, lt)` / `__ready`，兼容 huashu-art-motion 的渲染及 QA 工具。所有随机仅用于固定种子的音轨噪声；画面完全由时刻决定。

## 验收与素材

```bash
uv run --no-project --with playwright --with pillow --with numpy python demo/keynote-animation/verify.py
```

`verify.py` 在本地 `qa/` 保存完整解码、原尺寸确定性、慢动作帧差和播放器检查结果；渲染与审片的本地报告不提交。首版独立审片检查了 96 个半秒采样和五组转场；排除理由的可读性建议修订后复查通过。字体许可见 [assets/LICENSES.md](assets/LICENSES.md) 和 [assets/OFL.txt](assets/OFL.txt)。原始标志复制自 `docs/assets/logo.png`，配乐由 `score.py` 合成，无外部音频采样。

`demo/showcase/` 的 MP4、GIF 和封面是对外展示资产。本工程是它们的当前源；`demo/promo-showcase/` 保留为历史工程。QA、方向对比导出、WAV 和重复 MP4 已在本目录 `.gitignore` 排除，避免把本地报告与重复产物发布到站点。
