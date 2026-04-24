# boss-agent-cli 终端演示录制脚本
# 使用方式: vhs demo.tape
# 输出: demo.gif

Output demo.gif
Set Shell "zsh"
Set FontSize 14
Set Width 1200
Set Height 700
Set Theme "Catppuccin Mocha"
Set Padding 20
Set TypingSpeed 35ms

# ══════════════════════════════════════════════════
# Act 1: 开场 — 工具概览
# ══════════════════════════════════════════════════
Sleep 500ms
Type "# boss-agent-cli — 专为 AI Agent 设计的 BOSS 直聘求职 CLI"
Enter
Sleep 1s
Type "boss --help"
Sleep 300ms
Enter
Sleep 3s

Ctrl+L
Sleep 300ms

# ══════════════════════════════════════════════════
# Act 2: AI 自描述协议 — 33 个顶层命令分组展示
# ══════════════════════════════════════════════════
Type "# AI 自描述：33 top-level commands + 错误自愈 + JSON 信封协议"
Enter
Sleep 800ms
Type "boss --json schema 2>/dev/null | python3 demo_showcase.py"
Sleep 300ms
Enter
Sleep 5s

Ctrl+L
Sleep 300ms

# ══════════════════════════════════════════════════
# Act 3: 环境诊断 — 凭据完整性 + 网络 + 依赖
# ══════════════════════════════════════════════════
Type "# 一键诊断：环境 / 凭据完整性 / CDP / 网络"
Enter
Sleep 800ms
Type "boss doctor"
Sleep 300ms
Enter
Sleep 4s

Ctrl+L
Sleep 300ms

# ══════════════════════════════════════════════════
# Act 4: 四级登录降级
# ══════════════════════════════════════════════════
Type "# 登录：Cookie → CDP → QR httpx → patchright 四级降级"
Enter
Sleep 800ms
Type "boss status"
Sleep 300ms
Enter
Sleep 3s

Ctrl+L
Sleep 300ms

# ══════════════════════════════════════════════════
# Act 5: 搜索 + 福利筛选（核心功能）
# ══════════════════════════════════════════════════
Type "# 核心功能：搜索 + 福利精准筛选"
Enter
Sleep 800ms
Type "boss search --help"
Sleep 300ms
Enter
Sleep 4s

Ctrl+L
Sleep 300ms

# ══════════════════════════════════════════════════
# Act 6: 城市列表 + 配置管理
# ══════════════════════════════════════════════════
Type "# 40 个城市 + 运行时配置管理"
Enter
Sleep 800ms
Type "boss cities"
Sleep 300ms
Enter
Sleep 3s

Ctrl+L
Sleep 300ms

# ══════════════════════════════════════════════════
# Act 7: 流水线展示
# ══════════════════════════════════════════════════
Type "# 求职流水线 + 每日摘要 — Agent 自动追踪进度"
Enter
Sleep 800ms
Type "boss config list"
Sleep 300ms
Enter
Sleep 3s

Sleep 2s
