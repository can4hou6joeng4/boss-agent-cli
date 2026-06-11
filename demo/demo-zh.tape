# boss-agent-cli 终端演示（中文版） — 复刻 Trellis 风格 30fps/1280px
# 使用方式: vhs demo/demo-zh.tape
# 输出: demo/demo-zh.gif

Output demo/demo-zh.gif

Set Shell "zsh"
Set FontSize 14
Set Width 1280
Set Height 720
Set Framerate 30
Set Theme "Catppuccin Mocha"
Set Padding 20
Set TypingSpeed 35ms

# 隐藏环境准备：把仓库 venv 加入 PATH，让 `boss` 可直接调用
Hide
Type "export PATH=$PWD/.venv/bin:$PATH && clear"
Enter
Sleep 500ms
Show

# ══════════════════════════════════════════════════
# Act 1: 开场 — 工具定位
# ══════════════════════════════════════════════════
Sleep 600ms
Type "# boss-agent-cli — 专为 AI Agent 设计的 BOSS 直聘求职 CLI"
Enter
Sleep 1s
Type "boss --version"
Sleep 300ms
Enter
Sleep 1500ms

Type "boss --help"
Sleep 300ms
Enter
Sleep 4s

Ctrl+L
Sleep 400ms

# ══════════════════════════════════════════════════
# Act 2: AI 自描述协议 — 34 个命令分组
# ══════════════════════════════════════════════════
Type "# AI 自描述：34 顶层命令 + 错误自愈 + JSON 信封协议"
Enter
Sleep 800ms
Type "boss --json schema 2>/dev/null | python3 demo/demo_showcase.py"
Sleep 300ms
Enter
Sleep 6s

Ctrl+L
Sleep 400ms

# ══════════════════════════════════════════════════
# Act 3: 环境诊断 — 凭据 / 网络 / 浏览器
# ══════════════════════════════════════════════════
Type "# 一键诊断：环境 / 凭据完整性 / CDP / 网络 13 项检查"
Enter
Sleep 800ms
Type "boss doctor"
Sleep 300ms
Enter
Sleep 5s

Ctrl+L
Sleep 400ms

# ══════════════════════════════════════════════════
# Act 4: 四级登录降级链路
# ══════════════════════════════════════════════════
Type "# 登录链路：Cookie → CDP → QR httpx → patchright 四级降级"
Enter
Sleep 800ms
Type "boss status"
Sleep 300ms
Enter
Sleep 3s

Ctrl+L
Sleep 400ms

# ══════════════════════════════════════════════════
# Act 5: 搜索 + 福利精准筛选（核心）
# ══════════════════════════════════════════════════
Type "# 核心：关键词搜索 + 8 维筛选 + --welfare 真匹配"
Enter
Sleep 800ms
Type "boss search --help"
Sleep 300ms
Enter
Sleep 5s

Ctrl+L
Sleep 400ms

# ══════════════════════════════════════════════════
# Act 6: 城市覆盖
# ══════════════════════════════════════════════════
Type "# 40 个城市覆盖"
Enter
Sleep 800ms
Type "boss cities"
Sleep 300ms
Enter
Sleep 4s

Ctrl+L
Sleep 400ms

# ══════════════════════════════════════════════════
# Act 7: 流水线 + 配置闭环
# ══════════════════════════════════════════════════
Type "# 求职流水线 + 每日摘要 — Agent 自动追踪进度"
Enter
Sleep 800ms
Type "boss config list"
Sleep 300ms
Enter
Sleep 3500ms

Sleep 1500ms
