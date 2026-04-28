# Roadmap

This document tracks the medium-term and long-term direction of `boss-agent-cli`. Issues and PRs are welcome for any of the areas below.

## Released

- ✅ v1.8.x (2026-04-20, rapid patch series): strict mypy whitelist expanded from 3 to 61 modules (81% coverage), Python embedding API, `ai interview-prep` / `chat-coach`, Markdown digest output, Cursor/Windsurf integration, four additional AI provider entry points, and the English contribution guide
- ✅ v1.8.0 (2026-04-19): AI communication and interview expansion (`ai interview-prep` / `ai chat-coach`) plus protocol server growth to 43 tools
- ✅ v1.7.0 (2026-04-17): draft chat replies, application funnel analytics, and protocol server growth to 41 tools

Full release history lives in [CHANGELOG.md](CHANGELOG.md).

## Near term (current mainline)

### Data visualization

- [x] `boss stats --format html` outputs an interactive funnel report (v1.7.1)
- [x] `boss digest --format md` can be pasted directly into email or Feishu workflows (v1.8.1)
- [x] codecov badge integrated into the README (v1.7.1)

### Agent integration

- [x] MCP server supports HTTP streaming, SSE, and stdio transports (2026-04-27, PR #160)
- [x] host-specific integration examples for Codex, Cursor, and Windsurf (v1.8.1, `docs/integrations/` now fully covered)
- [x] OpenAI Functions export via `boss schema --format openai-tools` (v1.7.1)

### AI capabilities

- [x] `boss ai chat-coach` - communication guidance derived from chat history (v1.8.0)
- [x] `boss ai interview-prep` - mock interview generation based on the JD (v1.8.0)
- [x] support for current Claude 4.7 / GPT-5 generation models (v1.8.2, providers extended to openrouter, qwen, zhipu, and siliconflow)

## Mid term (v2.0)

### Architecture evolution

- [x] full mypy strict-mode rollout - **100% complete** (66/66 business modules now enforce `disallow_untyped_defs + disallow_any_generics + warn_return_any`, v1.9.1)
- [x] exported type signatures in `stubs/` for downstream IDE consumers (v1.8.6, including `py.typed`, canonical `__all__`, and 16 contract tests)
- [ ] evaluate a Bridge protocol move from HTTP/WS to gRPC - research completed (Issue #96, [docs/research/bridge-grpc.md](docs/research/bridge-grpc.md)), with the current conclusion set to **do not migrate yet** because localhost single-user scenarios do not gain meaningful performance, MV3 extension compatibility risk stays high, and dependency size would grow by about 8 MB. Five re-evaluation triggers are already documented.

### Ecosystem expansion

- [ ] Web UI (React + Tailwind) for non-agent users
- [ ] browser extension with deeper integration into the native BOSS Zhipin pages
- [ ] multi-platform support for Lagou / Zhilian / Liepin adapters - API research is fully complete (Issue #90 closed, [docs/research/platforms/](docs/research/platforms/)). Current conclusion: **Zhilian is the v2.0 priority candidate** with a 2-3 week implementation window, while Lagou and Liepin are not recommended to pursue.
  - [x] Week 1a: Platform ABC skeleton + `BossPlatform` adapter (#129, zero behavior change)
  - [x] Week 1b: global `--platform` CLI option + `get_platform_instance` helper + schema exposure of `current_platform`
  - [x] Week 1c: command-layer migration to the Platform interface (**20 commands**: `greet`, `apply`, `batch-greet`, `interviews`, `detail`, `show`, `me`, `recommend`, `chat`, `chatmsg`, `mark`, `exchange`, `pipeline`, `digest`, `search`, `export`, `chat_summary`, `history`, `status`, `watch`)
  - [x] Week 1d: `ZhilianPlatform` stub registered in the platform registry (abstraction self-proof with full envelope adaptation; P0/P1/P2 still raise `NotImplementedError`)
  - [x] Week 2: Zhilian read-only implementation (`search`, `detail`, `recommend`, `user_info`)
  - [x] Week 3: Zhilian write operations (`greet`, `apply`) + docs + MCP adaptation
  - [ ] Week 4: product decision on recruiter-side support and whether to onboard it at all (candidate-side support is the only runtime path today)

### Community building

- [ ] richer Chinese and English demo assets and launch materials (the repo already ships `demo.gif` and `demo.tape` terminal demos)
- [ ] follow up on the review outcome of [awesome-agents](https://github.com/kyrolabs/awesome-agents) PR #423
- [ ] decide later whether to pursue [awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code)
- [x] English contribution guide (`CONTRIBUTING.en.md`, v1.8.3)

## Long-term vision

**Make AI agents feel like real job-search copilots**, not just tool-call wrappers:

- agents should autonomously complete the full loop from search to screening to greeting to follow-up to interview prep
- users should only need to describe a target, such as "find remote Python roles above 30K", and let the agent execute the workflow
- all data should remain local-first, with privacy and compliance treated as primary constraints

## How to contribute

1. Pick up an item labeled `good first issue` or `help wanted`
2. Open an issue when you want to discuss a direction or design before implementation
3. Send a PR directly for bugs or documentation fixes
4. Non-code help is also useful: test reports, usage feedback, and translations

See [CONTRIBUTING.en.md](CONTRIBUTING.en.md).

---

> The roadmap is a living document and should be updated alongside each minor release.
