# boss-agent-cli 终端演示录制脚本
# 使用方式: brew install vhs && vhs demo.tape
# 输出: demo.gif（用于 README 展示）

Output demo.gif
Set Shell "zsh"
Set FontSize 15
Set Width 1200
Set Height 600
Set Theme "Catppuccin Mocha"
Set Padding 20
Set TypingSpeed 60ms

# ── 环境检查 ──────────────────────────────────
Sleep 500ms
Type "boss doctor"
Sleep 300ms
Enter
Sleep 3s

# ── 搜索职位（福利筛选） ──────────────────────
Type 'boss search "Golang" --city 广州 --welfare "双休,五险一金"'
Sleep 300ms
Enter
Sleep 5s

# ── 查看搜索结果详情 ──────────────────────────
Type "boss show 1"
Sleep 300ms
Enter
Sleep 3s

# ── 获取个性化推荐 ────────────────────────────
Type "boss recommend"
Sleep 300ms
Enter
Sleep 4s

# ── 导出结果 ──────────────────────────────────
Type 'boss export "Golang" --city 广州 -o jobs.csv'
Sleep 300ms
Enter
Sleep 3s

# ── 查看沟通列表 ──────────────────────────────
Type "boss chat --from boss --days 7"
Sleep 300ms
Enter
Sleep 3s

Sleep 2s
