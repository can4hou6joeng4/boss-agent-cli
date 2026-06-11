# boss-agent-cli terminal demo (English) — Trellis-style 30fps/1280px
# Usage: vhs demo/demo-en.tape
# Output: demo/demo-en.gif

Output demo/demo-en.gif

Set Shell "zsh"
Set FontSize 14
Set Width 1280
Set Height 720
Set Framerate 30
Set Theme "Catppuccin Mocha"
Set Padding 20
Set TypingSpeed 35ms

Hide
Type "export PATH=$PWD/.venv/bin:$PATH && clear"
Enter
Sleep 500ms
Show

# ══════════════════════════════════════════════════
# Act 1: Pitch — what is this CLI
# ══════════════════════════════════════════════════
Sleep 600ms
Type "# boss-agent-cli — a CLI built for AI Agents to drive BOSS Zhipin"
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
# Act 2: Self-describing protocol — 34 top-level commands
# ══════════════════════════════════════════════════
Type "# Schema-driven: 34 commands + auto-recoverable errors + JSON envelope"
Enter
Sleep 800ms
Type "boss --json schema 2>/dev/null | python3 demo/demo_showcase_en.py"
Sleep 300ms
Enter
Sleep 6s

Ctrl+L
Sleep 400ms

# ══════════════════════════════════════════════════
# Act 3: Diagnostics — env / credentials / network
# ══════════════════════════════════════════════════
Type "# One-shot diagnostics: env / credentials / CDP / network (13 checks)"
Enter
Sleep 800ms
Type "boss doctor"
Sleep 300ms
Enter
Sleep 5s

Ctrl+L
Sleep 400ms

# ══════════════════════════════════════════════════
# Act 4: 4-tier login fallback chain
# ══════════════════════════════════════════════════
Type "# Login chain: Cookie -> CDP -> QR httpx -> patchright (4-tier fallback)"
Enter
Sleep 800ms
Type "boss status"
Sleep 300ms
Enter
Sleep 3s

Ctrl+L
Sleep 400ms

# ══════════════════════════════════════════════════
# Act 5: Search + welfare filter (core feature)
# ══════════════════════════════════════════════════
Type "# Core: keyword search + 8-dimension filter + --welfare AND-matching"
Enter
Sleep 800ms
Type "boss search --help"
Sleep 300ms
Enter
Sleep 5s

Ctrl+L
Sleep 400ms

# ══════════════════════════════════════════════════
# Act 6: City coverage
# ══════════════════════════════════════════════════
Type "# 40 cities supported"
Enter
Sleep 800ms
Type "boss cities"
Sleep 300ms
Enter
Sleep 4s

Ctrl+L
Sleep 400ms

# ══════════════════════════════════════════════════
# Act 7: Pipeline + config closure
# ══════════════════════════════════════════════════
Type "# Pipeline + daily digest — agents track progress automatically"
Enter
Sleep 800ms
Type "boss config list"
Sleep 300ms
Enter
Sleep 3500ms

Sleep 1500ms
