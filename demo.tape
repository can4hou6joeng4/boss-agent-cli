# boss-agent-cli 终端演示录制脚本
# 使用方式: vhs demo.tape
# 输出: demo.gif

Output demo.gif
Set Shell "zsh"
Set FontSize 14
Set Width 1200
Set Height 620
Set Theme "Catppuccin Mocha"
Set Padding 20
Set TypingSpeed 50ms

# ── 工具介绍 ──────────────────────────────────
Sleep 500ms
Type "boss --help"
Sleep 300ms
Enter
Sleep 3s

# ── 能力自描述 ────────────────────────────────
Type "boss schema 2>/dev/null | python3 -m json.tool | head -40"
Sleep 300ms
Enter
Sleep 4s

# ── 环境诊断 ──────────────────────────────────
Type "boss doctor 2>/dev/null | python3 -m json.tool"
Sleep 300ms
Enter
Sleep 4s

# ── 城市列表 ──────────────────────────────────
Type "boss cities 2>/dev/null | python3 -m json.tool | head -30"
Sleep 300ms
Enter
Sleep 3s

# ── 搜索示例（展示命令格式） ──────────────────
Type "# 搜索广州 Golang 职位，要求双休+五险一金"
Sleep 300ms
Enter
Sleep 1s
Type 'boss search "Golang" --city 广州 --welfare "双休,五险一金"'
Sleep 500ms
Enter
Sleep 4s

Sleep 2s
