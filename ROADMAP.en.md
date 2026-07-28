# Roadmap

This document tracks the medium-term and long-term direction of `boss-agent-cli`. Issues and PRs are welcome for any of the areas below.

## Released

- ✅ v1.18.0 (2026-07-29): usable Docker / Compose image with CI build gate, MCP schema contract fixes and `mcp_server` split, offline evals in P0, pinned ruff rules, and dependency refresh (rich 15)
- ✅ v1.17.0 (2026-07-27): `boss favorites list/sync` for syncing saved jobs, the restricted Research Mode resumable crawl workflow, and internship job-type fields plus the internship filter mapping fix
- ✅ v1.16.0 (2026-07-17): explicit `operating_mode` two-mode contract (`assisted` / `research`) and the compliance guardrail rebuilt as an immutable capability policy registry, with CLI, schema, and MCP filtering all derived from one source of truth
- ✅ v1.15.0 (2026-07-14): `boss ai cover-letter` drafts, removal of dead config keys and dead code, and hardened Zhilian page host validation (exact hostname matching)
- ✅ v1.14.0 (2026-06-25): local shortlist tags / notes / offline comparison, `ai fit` / `ai suggest-keywords` / `ai resume-optimize`, the MCP-first repositioning, and search `match_score`
- ✅ v1.13.x (2026-06-11 – 06-16): platform capability-status semantics, complete login error codes, local caching for welfare job descriptions, and the bilingual README rebuilt as a navigation hub; the Agent Skill moved to the standalone [boss-skill](https://github.com/can4hou6joeng4/boss-skill) repo (breaking change)
- ✅ v1.12.0 (2026-06-09): MCP stdio / SSE / HTTP streaming transports, `boss_export`, and the 51job (`qiancheng`) placeholder adapter
- ✅ v1.11.0 (2026-04-23): recruiter mode (`--role recruiter`) with the full recruiter CLI command group, the dual-channel `BossRecruiterClient`, and the `RecruiterPlatform` abstraction
- ✅ v1.10.x (2026-04-21): the Platform abstraction landing — ABC plus registry plus the global `--platform` option, with all 20 commands migrated so nothing under `commands/` references `BossClient` directly
- ✅ v1.9.x (2026-04-20): full mypy strict-mode rollout (66/66 business modules), the Python embedding API, and `py.typed` type exports
- ✅ v1.8.x (2026-04-19 – 04-20): AI communication and interview expansion (`ai interview-prep` / `ai chat-coach`), Cursor / Windsurf integration, and the English contribution guide
- ✅ v1.7.0 (2026-04-17): draft chat replies and application funnel analytics

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

### Governance and compliance

- [x] default low-risk assistance mode: sensitive commands are blocked by default and handed back to the official platform (ADR 0001)
- [x] explicit `operating_mode` two-mode contract: `assisted` / `research` drives the CLI, schema, and MCP filtering from a single source of truth (v1.16.0, ADR 0002)
- [x] restricted Research Mode collection: fixed request / detail / wall-clock / retry budgets, SQLite checkpointing, and a stop switch (v1.17.0)

### Architecture evolution

- [x] full mypy strict-mode rollout - **100% complete** (66/66 business modules now enforce `disallow_untyped_defs + disallow_any_generics + warn_return_any`, v1.9.1)
- [x] exported type signatures in `stubs/` for downstream IDE consumers (v1.8.6, including `py.typed`, canonical `__all__`, and 16 contract tests)
- [ ] evaluate a Bridge protocol move from HTTP/WS to gRPC - research completed (Issue #96, [docs/research/bridge-grpc.md](docs/research/bridge-grpc.md)), with the current conclusion set to **do not migrate yet** because localhost single-user scenarios do not gain meaningful performance, MV3 extension compatibility risk stays high, and dependency size would grow by about 8 MB. Five re-evaluation triggers are already documented.

### Ecosystem expansion

- [ ] Web UI (React + Tailwind) for non-agent users
- [ ] browser extension with deeper integration into the native BOSS Zhipin pages
- [ ] multi-platform support for Lagou / Zhilian / Liepin adapters - API research is fully complete (Issue #90 closed, [docs/research/platforms/](docs/research/platforms/)). Conclusion: **Zhilian candidate-side is implemented** (read-only search/detail/recommend/user_info + write greet/apply, see the Week 2-3 sub-items below); Lagou and Liepin are not pursued after evaluation; 51job remains in the research backlog.
  - [x] Week 1a: Platform ABC skeleton + `BossPlatform` adapter (#129, zero behavior change)
  - [x] Week 1b: global `--platform` CLI option + `get_platform_instance` helper + schema exposure of `current_platform`
  - [x] Week 1c: command-layer migration to the Platform interface (**20 commands**: `greet`, `apply`, `batch-greet`, `interviews`, `detail`, `show`, `me`, `recommend`, `chat`, `chatmsg`, `mark`, `exchange`, `pipeline`, `digest`, `search`, `export`, `chat_summary`, `history`, `status`, `watch`)
  - [x] Week 1d: `ZhilianPlatform` stub registered in the platform registry (abstraction self-proof with full envelope adaptation; P0/P1/P2 still raise `NotImplementedError`)
  - [x] Week 2: Zhilian read-only implementation (`search`, `detail`, `recommend`, `user_info`)
  - [x] Week 3: Zhilian write operations (`greet`, `apply`) + docs + MCP adaptation
  - [x] Week 4: recruiter-side capability evaluation complete → **not onboarding for now** (0 of 4 onboarding conditions met; the `RecruiterPlatform` skeleton stays in place pending community signal, see `docs/research/platforms/zhaopin-recruiter-evaluation.md`)
  - [ ] 51job: the placeholder adapter has landed (every capability returns a stable `NOT_SUPPORTED` envelope, v1.12.0–v1.13.0), but the real interface stays in the research backlog until the candidate-side read-only entry points and redacted fixtures are clear enough for a runtime adapter ([docs/research/platforms/51job.md](docs/research/platforms/51job.md))

### Community building

- [ ] richer Chinese and English demo assets and launch materials (the repo already ships `demo/demo-zh.gif` / `demo/demo-en.gif` plus the matching `demo/demo-zh.tape` / `demo/demo-en.tape` terminal demos)
- [x] listed in [awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers) (PR #4992, merged 2026-04-26)
- [x] [awesome-agents](https://github.com/kyrolabs/awesome-agents) PR #423 resolved — closed by a bot with no review, and the same section shows a pattern of silent closures, so the conclusion is to not resubmit for now (see `docs/marketing/awesome-submissions.md`)
- [ ] decide later whether to pursue [awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code) (that repo accepts Web UI issue forms only and forbids the gh CLI)
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
