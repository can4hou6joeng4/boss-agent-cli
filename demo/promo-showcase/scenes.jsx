// scenes.jsx — the six shots of the boss-agent-cli promo. Each Scene returns a
// <Shot> and is gated to its time window by a <Sprite> in app.jsx, so all
// `at` timings below are sprite-local seconds.
const {
	C, Appear, Shot, Caret, Prompt, CmdLine,
	J, JLine, note, Caption, Chip, AiRow, PlatformPill, Stat, Counter,
} = window;

const Wordmark = ({ size }) => (
	<span className="mono" style={{ fontSize: size, fontWeight: 700, color: C.text, letterSpacing: '-0.02em' }}>
		boss-<span style={{ color: C.accent }}>agent</span>-cli
	</span>
);

// ── Scene 1 — title card (0–4.5s) ──
function Scene1() {
	return (
		<Shot>
			<div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
				<div className="mono" style={{ fontSize: 26, color: C.dim, marginBottom: 36 }}>
					<Prompt /><window.Typer text="boss" delay={0.35} cps={9} />
				</div>
				<Appear at={1.15} dur={0.6} y={20} scale={0.96}>
					<Wordmark size={104} />
				</Appear>
				<Appear at={1.5} dur={0.6} y={16}>
					<div className="sans" style={{ fontSize: 30, color: C.dim, marginTop: 20, textAlign: 'center' }}>
						专为 AI Agent 设计的 BOSS 直聘本地辅助 CLI 工具
					</div>
				</Appear>
				<div style={{ display: 'flex', gap: 14, marginTop: 36 }}>
					<Chip tone="accent" at={2.0}>本地辅助</Chip>
					<Chip tone="cyan" at={2.15}>只读优先</Chip>
					<Chip tone="green" at={2.3}>用户主动触发</Chip>
				</div>
				<Appear at={2.65} dur={0.5}>
					<div className="mono" style={{ fontSize: 20, color: C.faint, marginTop: 30, letterSpacing: '0.04em' }}>v1.13.1 · MIT · Python ≥ 3.10</div>
				</Appear>
			</div>
		</Shot>
	);
}

// ── Scene 2 — search + welfare filtering (4.5–11s) ──
function Scene2() {
	const j0 = 3.0;
	const jl = (i) => j0 + i * 0.13;
	return (
		<Shot>
			<CmdLine text={'boss search "Golang" --city 广州 --welfare "双休,五险一金"'} delay={0.2} cps={34} size={30} />
			<div className="mono" style={{ fontSize: 22, color: C.faint, lineHeight: 1.65, marginTop: 8 }}>
				<Appear at={2.0} dur={0.2}><div>· auth ok   · throttle 1.8s</div></Appear>
				<Appear at={2.25} dur={0.2}><div>· fetch page 1 … 8 hits</div></Appear>
				<Appear at={2.5} dur={0.2}><div>· fetch page 2 … <span style={{ color: C.dim }}>16 hits（福利补抓）</span></div></Appear>
				<Appear at={2.75} dur={0.2}><div>· match <span style={{ color: C.accent }}>双休 ∧ 五险一金</span> → <span style={{ color: C.green }}>15 / 24</span></div></Appear>
			</div>
			<div className="mono" style={{ fontSize: 25, color: C.text, lineHeight: 1.45, marginTop: 18 }}>
				<JLine at={jl(0)}>{J([['p', '{']])}</JLine>
				<JLine at={jl(1)} indent={1}>{J([['k', '"ok"'], ['p', ': '], ['n', 'true'], ['p', ',']])}</JLine>
				<JLine at={jl(2)} indent={1}>{J([['k', '"command"'], ['p', ': '], ['s', '"search"'], ['p', ',']])}</JLine>
				<JLine at={jl(3)} indent={1}>{J([['k', '"data"'], ['p', ': [']])}</JLine>
				<JLine at={jl(4)} indent={2}>{J([['p', '{ '], ['k', '"title"'], ['p', ': '], ['s', '"高级 Golang 工程师"'], ['p', ', '], ['k', '"salary"'], ['p', ': '], ['s', '"30-50K·16薪"'], ['p', ',']])}</JLine>
				<JLine at={jl(5)} indent={3}>{J([['k', '"welfare"'], ['p', ': [']])}{J([['h', '"双休"']])}{J([['p', ', ']])}{J([['h', '"五险一金"']])}{J([['p', ', '], ['s', '"补充医疗"'], ['p', '] },']])}</JLine>
				<JLine at={jl(6)} indent={2}>{J([['c', '{ … 14 more … }']])}</JLine>
				<JLine at={jl(7)} indent={1}>{J([['p', '],']])}</JLine>
				<JLine at={jl(8)} indent={1}>{J([['k', '"pagination"'], ['p', ': { '], ['k', '"total"'], ['p', ': '], ['n', '15'], ['p', ', '], ['k', '"has_more"'], ['p', ': '], ['n', 'true'], ['p', ' },']])}</JLine>
				<JLine at={jl(9)} indent={1}>{J([['k', '"hints"'], ['p', ': { '], ['k', '"next_actions"'], ['p', ': ['], ['s', '"boss detail <id>"'], ['p', '] }']])}</JLine>
				<JLine at={jl(10)}>{J([['p', '}']])}</JLine>
			</div>
			<Caption at={jl(8)} kicker="职位发现 · search" title="多条件 AND 福利筛选" sub="自动翻页补抓 · 真实匹配，不是关键词命中" />
		</Shot>
	);
}

// ── Scene 3 — schema + the JSON envelope (11–16.5s) ──
function Scene3() {
	return (
		<Shot>
			<CmdLine text={'boss schema --format anthropic-tools'} delay={0.2} cps={32} size={30} />
			<Appear at={1.3} dur={0.35}>
				<div className="mono" style={{ fontSize: 23, color: C.faint, marginTop: 4, marginBottom: 10 }}>→ 自描述能力清单 · Agent 的能力真源</div>
			</Appear>
			<div className="mono" style={{ fontSize: 26, color: C.text, lineHeight: 1.5 }}>
				<JLine at={1.6}>{J([['p', '{']])}</JLine>
				<JLine at={1.75} indent={1}>{J([['k', '"ok"'], ['p', ': '], ['n', 'true'], ['p', ',']])}{note('成败一目了然', 2.4)}</JLine>
				<JLine at={1.9} indent={1}>{J([['k', '"schema_version"'], ['p', ': '], ['s', '"1.0"'], ['p', ',']])}</JLine>
				<JLine at={2.05} indent={1}>{J([['k', '"command"'], ['p', ': '], ['s', '"search"'], ['p', ',']])}</JLine>
				<JLine at={2.2} indent={1}>{J([['k', '"data"'], ['p', ': [ … ],']])}{note('结构化结果', 2.7)}</JLine>
				<JLine at={2.35} indent={1}>{J([['k', '"pagination"'], ['p', ': { … },']])}</JLine>
				<JLine at={2.5} indent={1}>{J([['k', '"error"'], ['p', ': '], ['n', 'null'], ['p', ',']])}{note('code · recoverable · recovery_action', 3.0)}</JLine>
				<JLine at={2.65} indent={1}>{J([['k', '"hints"'], ['p', ': { '], ['k', '"next_actions"'], ['p', ': [ … ] }']])}{note('下一步该调用什么', 3.3)}</JLine>
				<JLine at={2.8}>{J([['p', '}']])}</JLine>
			</div>
			<Appear at={3.5} dur={0.5}>
				<div className="mono" style={{ marginTop: 26, fontSize: 24, color: C.dim }}>
					<span style={{ color: C.accent, fontWeight: 700, fontSize: 30 }}><Counter to={35} at={3.5} /></span> 顶层命令 · <span style={{ color: C.text }}>9</span> 招聘者子命令 · 导出 <span style={{ color: C.accent2 }}>openai-tools / anthropic-tools</span>
				</div>
			</Appear>
			<Caption at={3.1} kicker="能力真源 · schema-driven" title="统一 JSON 信封" sub="stdout 只放 JSON · Agent 一调用就懂" />
		</Shot>
	);
}

// ── Scene 4 — compliance guardrails (16.5–21.5s) ──
function Scene4() {
	const blocked = (
		<span style={{ color: C.amber, fontWeight: 700, background: 'color-mix(in oklch, var(--amber) 16%, transparent)', borderRadius: 5, padding: '1px 8px' }}>"COMPLIANCE_BLOCKED"</span>
	);
	return (
		<Shot>
			<CmdLine text={'boss greet <security_id>'} delay={0.2} cps={26} size={30} />
			<div className="mono" style={{ fontSize: 26, color: C.text, lineHeight: 1.5, marginTop: 12 }}>
				<JLine at={1.2}>{J([['p', '{']])}</JLine>
				<JLine at={1.35} indent={1}>{J([['k', '"ok"'], ['p', ': '], ['n', 'false'], ['p', ',']])}</JLine>
				<JLine at={1.5} indent={1}>{J([['k', '"command"'], ['p', ': '], ['s', '"greet"'], ['p', ',']])}</JLine>
				<JLine at={1.65} indent={1}>{J([['k', '"error"'], ['p', ': {']])}</JLine>
				<JLine at={1.85} indent={2}>{J([['k', '"code"'], ['p', ': ']])}{blocked}{J([['p', ',']])}</JLine>
				<JLine at={2.0} indent={2}>{J([['k', '"recoverable"'], ['p', ': '], ['n', 'true'], ['p', ',']])}</JLine>
				<JLine at={2.15} indent={2}>{J([['k', '"recovery_action"'], ['p', ': '], ['s', '"回到 BOSS 直聘官网手动完成"']])}</JLine>
				<JLine at={2.3} indent={1}>{J([['p', '}']])}</JLine>
				<JLine at={2.45}>{J([['p', '}']])}</JLine>
			</div>
			<Appear at={2.7} dur={0.4}>
				<div className="mono" style={{ fontSize: 22, color: C.faint, marginTop: 14 }}>exit 1 · 可程序化恢复，不是报错</div>
			</Appear>
			<div style={{ display: 'flex', gap: 12, marginTop: 24, flexWrap: 'wrap' }}>
				<Chip tone="green" at={3.0}>只读优先</Chip>
				<Chip tone="green" at={3.12}>不规避风控</Chip>
				<Chip tone="green" at={3.24}>不批量触达</Chip>
				<Chip tone="green" at={3.36}>不抓取数据</Chip>
			</div>
			<Caption at={2.4} kicker="合规护栏 · 默认开启" title="低风险辅助模式" sub="敏感动作交还官方平台，由用户手动完成" />
		</Shot>
	);
}

// ── Scene 5 — AI assist + multi-platform (21.5–26.5s) ──
function Scene5() {
	return (
		<Shot>
			<CmdLine text={'boss ai analyze-jd  --resume me.json  <security_id>'} delay={0.2} cps={36} size={30} />
			<div style={{ marginTop: 12 }}>
				<AiRow cmd="boss ai analyze-jd" desc="匹配度 · 能力缺口 · 关键词命中" at={1.2} />
				<AiRow cmd="boss ai polish" desc="STAR 量化 · 一键润色简历" at={1.45} />
				<AiRow cmd="boss ai interview-prep" desc="高频题 · 可能追问 · 复盘清单" at={1.7} />
			</div>
			<Appear at={2.2} dur={0.4}>
				<div className="mono" style={{ marginTop: 26, fontSize: 22, color: C.faint }}>--platform <span style={{ color: C.dim }}>双注册表 · 求职者 / 招聘者</span></div>
			</Appear>
			<div style={{ display: 'flex', gap: 16, marginTop: 16 }}>
				<PlatformPill at={2.5} name="zhipin" status="✅" note="求职者 + 招聘者" tone="green" />
				<PlatformPill at={2.65} name="zhilian" status="🟡" note="求职者读写已接通" tone="amber" />
				<PlatformPill at={2.8} name="qiancheng" status="🚧" note="占位 · NOT_SUPPORTED" tone="faint" />
			</div>
			<Caption at={2.05} kicker="AI 增强 · 多平台抽象" title="求职全链路本地辅助" sub="JD 分析 · 简历润色 · 模拟面试 · Platform 双注册表" />
		</Shot>
	);
}

// ── Scene 6 — outro / CTA (26.5–31s) ──
function Scene6() {
	return (
		<Shot>
			<div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
				<Appear at={0.1} dur={0.6} y={18} scale={0.96}>
					<Wordmark size={84} />
				</Appear>
				<div style={{ display: 'flex', gap: 12, marginTop: 26 }}>
					<Stat at={0.6} num={<Counter to={35} at={0.6} />} label="顶层命令" />
					<Stat at={0.72} num="1400+" label="测试" />
					<Stat at={0.84} num="MIT" label="开源协议" />
					<Stat at={0.96} num="CI" label="持续集成" />
				</div>
				<Appear at={1.3} dur={0.5} y={14}>
					<div className="mono" style={{ marginTop: 34, fontSize: 30, color: C.text, padding: '16px 26px', border: '1px solid var(--line)', borderRadius: 12, background: 'var(--panel)' }}>
						<span style={{ color: C.accent }}>$ </span>uv tool install boss-agent-cli<Caret />
					</div>
				</Appear>
				<Appear at={1.7} dur={0.5}>
					<div className="mono" style={{ marginTop: 22, fontSize: 26, color: C.accent2 }}>github.com/can4hou6joeng4/boss-agent-cli</div>
				</Appear>
				<Appear at={2.0} dur={0.5}>
					<div className="sans" style={{ marginTop: 20, fontSize: 20, color: C.faint, letterSpacing: '0.04em' }}>本地辅助 · 只读优先 · 用户主动触发 · 不规避风控 · 不批量触达</div>
				</Appear>
			</div>
		</Shot>
	);
}

Object.assign(window, { Scene1, Scene2, Scene3, Scene4, Scene5, Scene6 });
