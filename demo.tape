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
Set TypingSpeed 40ms

# ══════════════════════════════════════════════════
# Act 1: 全局概览 — 19 个命令，覆盖完整求职链
# ══════════════════════════════════════════════════
Sleep 500ms
Type "# AI Agent 求职自动化 — boss-agent-cli"
Enter
Sleep 1s
Type "boss --help"
Sleep 300ms
Enter
Sleep 3s

Ctrl+L
Sleep 300ms

# ══════════════════════════════════════════════════
# Act 2: AI 自描述协议 — Agent 一次调用理解全部能力
# ══════════════════════════════════════════════════
Type "# AI 自描述协议：一次调用 → 19 commands + 错误自愈 + JSON 输出"
Enter
Sleep 800ms
Type "boss --json schema 2>/dev/null | python3 demo_showcase.py"
Sleep 300ms
Enter
Sleep 5s

Ctrl+L
Sleep 300ms

# ══════════════════════════════════════════════════
# Act 3: 环境诊断 — 一键自检全覆盖
# ══════════════════════════════════════════════════
Type "# 一键诊断：Python / 浏览器 / 登录态 / 网络"
Enter
Sleep 800ms
Type "boss doctor"
Sleep 300ms
Enter
Sleep 4s

Ctrl+L
Sleep 300ms

# ══════════════════════════════════════════════════
# Act 4: 核心功能 — 搜索 + 福利精准筛选
# ══════════════════════════════════════════════════
Type "# 搜索 + 福利精准筛选（自动翻页 + 详情匹配，只返回命中结果）"
Enter
Sleep 800ms
Type 'boss search "Golang" --city 广州 --welfare "双休,五险一金"'
Sleep 500ms
Enter
Sleep 8s

Sleep 2s
