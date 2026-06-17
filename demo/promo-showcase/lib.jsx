// lib.jsx — design tokens, terminal chrome, and reusable time-driven helpers
// for the boss-agent-cli promo. Everything here is a pure function of the
// timeline (no CSS @keyframes) so the Stage exports frame-by-frame cleanly.
const { useSprite, useTime, Easing, clamp, interpolate } = window;

// Tokens mirror the CSS :root vars so inline styles share one source of truth.
const C = {
	bd:'var(--bd)', term:'var(--term)', termTop:'var(--term-top)', panel:'var(--panel)',
	line:'var(--line)', text:'var(--text)', dim:'var(--dim)', faint:'var(--faint)',
	accent:'var(--accent)', accent2:'var(--accent2)', green:'var(--green)',
	amber:'var(--amber)', red:'var(--red)', purple:'var(--purple)',
};
const MONO = '"JetBrains Mono", ui-monospace, SFMono-Regular, "PingFang SC", "Noto Sans SC", monospace';
const SANS = '-apple-system,"SF Pro Text","PingFang SC","Noto Sans SC",system-ui,sans-serif';

const eoc = Easing.easeOutCubic, eic = Easing.easeInCubic;

// ── Appear: fade + slide (+ optional scale) a block in at sprite-local `at` ──
function Appear({ at = 0, dur = 0.4, y = 14, x = 0, scale = 1, ease = eoc, style = {}, className, children }) {
	const { localTime } = useSprite();
	const t = ease(clamp((localTime - at) / dur, 0, 1));
	const sc = 1 + (scale - 1) * (1 - t);
	return (
		<div className={className} style={{
			opacity: t,
			transform: `translate(${(1 - t) * x}px, ${(1 - t) * y}px) scale(${sc})`,
			...style,
		}}>{children}</div>
	);
}

// ── Shot: uniform per-scene fade-in / fade-out wrapper ──
function Shot({ fadeIn = 0.32, fadeOut = 0.3, children }) {
	const { localTime, duration } = useSprite();
	let op = 1;
	if (localTime < fadeIn) op = eoc(clamp(localTime / fadeIn, 0, 1));
	else if (localTime > duration - fadeOut) op = 1 - eic(clamp((localTime - (duration - fadeOut)) / fadeOut, 0, 1));
	return <div style={{ position: 'absolute', inset: 0, opacity: op }}>{children}</div>;
}

// ── Caret: typing (solid) / resting (blinking) block cursor ──
function Caret({ solid = false }) {
	const t = useTime();
	const on = solid || (Math.floor(t * 1.8) % 2 === 0);
	return <span style={{ color: C.accent, opacity: on ? 1 : 0 }}>▋</span>;
}

// ── Typer: typewriter reveal; solid caret while typing, blinks briefly after ──
function Typer({ text, delay = 0, cps = 40, color, caret = true }) {
	const { localTime } = useSprite();
	const elapsed = Math.max(0, localTime - delay);
	const n = Math.floor(clamp(elapsed * cps, 0, text.length));
	const typeDur = text.length / cps;
	let caretEl = null;
	if (caret) {
		if (elapsed < typeDur) caretEl = <Caret solid />;
		else if (elapsed < typeDur + 0.6) caretEl = <Caret />;
	}
	return <span style={{ color }}>{text.slice(0, n)}{caretEl}</span>;
}

// ── Prompt + command line ──
function Prompt() {
	return <span><span style={{ color: C.green }}>~/work</span> <span style={{ color: C.accent }}>❯</span> </span>;
}
function CmdLine({ text, delay = 0, cps = 38, size = 30 }) {
	return (
		<div className="mono" style={{ fontSize: size, color: C.text, marginBottom: 8, whiteSpace: 'pre' }}>
			<Prompt /><Typer text={text} delay={delay} cps={cps} />
		</div>
	);
}

// ── JSON tokens: J([[type,text],...]) → colored spans ──
// types: k=key  s=string  n=number/bool/null  p=punct/dim  c=comment  h=highlight
function J(tokens) {
	const colorOf = { k: C.accent2, s: C.green, n: C.amber, p: C.dim, c: C.faint, h: C.green };
	return tokens.map(([ty, tx], i) => {
		const st = { color: colorOf[ty] };
		if (ty === 'c') st.fontStyle = 'italic';
		if (ty === 'h') { st.fontWeight = 700; st.background = 'color-mix(in oklch, var(--green) 18%, transparent)'; st.borderRadius = 5; st.padding = '1px 6px'; }
		return <span key={i} style={st}>{tx}</span>;
	});
}

// ── JLine: one streamed JSON line (fades in at sprite-local `at`) ──
function JLine({ indent = 0, at = 0, children }) {
	return (
		<Appear at={at} dur={0.2} y={5}>
			<div style={{ paddingLeft: `${indent * 1.7}em` }}>{children}</div>
		</Appear>
	);
}

// ── note: an inline annotation that slides in after its line ──
function note(text, at) {
	return (
		<Appear at={at} dur={0.4} x={14} style={{ display: 'inline-block', marginLeft: '2.2em', verticalAlign: 'baseline' }}>
			<span className="sans" style={{ color: C.dim, fontSize: 22 }}>
				<span style={{ color: C.accent }}>→ </span>{text}
			</span>
		</Appear>
	);
}

// ── Caption: lower-left kicker / title / sub ──
function Caption({ kicker, title, sub, at = 0 }) {
	return (
		<div style={{ position: 'absolute', left: 0, bottom: 0, maxWidth: '92%' }}>
			<Appear at={at} dur={0.5} y={18}>
				<div className="sans" style={{ color: C.accent, fontSize: 19, fontWeight: 700, letterSpacing: '0.22em', textTransform: 'uppercase', marginBottom: 10 }}>{kicker}</div>
				<div className="sans" style={{ color: C.text, fontSize: 38, fontWeight: 700, letterSpacing: '-0.01em', lineHeight: 1.15 }}>{title}</div>
				{sub && <div className="sans" style={{ color: C.dim, fontSize: 22, marginTop: 9, lineHeight: 1.5 }}>{sub}</div>}
			</Appear>
		</div>
	);
}

// ── Chip: rounded status pill ──
function Chip({ children, tone = 'dim', at, dur = 0.4 }) {
	const colorMap = { dim: C.dim, accent: C.accent, green: C.green, amber: C.amber, red: C.red, cyan: C.accent2, purple: C.purple };
	const col = colorMap[tone] || C.dim;
	const inner = (
		<span className="mono" style={{
			display: 'inline-flex', alignItems: 'center', gap: 8,
			padding: '8px 16px', borderRadius: 999,
			border: `1px solid color-mix(in oklch, ${col} 45%, var(--line))`,
			background: `color-mix(in oklch, ${col} 12%, transparent)`,
			color: col, fontSize: 22, whiteSpace: 'nowrap',
		}}>{children}</span>
	);
	return at != null
		? <Appear at={at} dur={dur} y={10} style={{ display: 'inline-block' }}>{inner}</Appear>
		: inner;
}

// ── AiRow: `❯ cmd  →  description` row ──
function AiRow({ cmd, desc, at }) {
	return (
		<Appear at={at} dur={0.35} y={10} className="mono" style={{ display: 'flex', gap: 16, alignItems: 'baseline', fontSize: 27, marginBottom: 15 }}>
			<span style={{ color: C.text }}><Prompt />{cmd}</span>
			<span style={{ color: C.accent }}>→</span>
			<span style={{ color: C.dim }}>{desc}</span>
		</Appear>
	);
}

// ── PlatformPill: platform card with status glyph + note ──
function PlatformPill({ name, status, note: noteText, tone = 'faint', at = 0 }) {
	const colorMap = { green: C.green, amber: C.amber, faint: C.faint, cyan: C.accent2, purple: C.purple };
	const col = colorMap[tone] || C.faint;
	return (
		<Appear at={at} dur={0.45} y={12} className="mono" style={{
			display: 'flex', flexDirection: 'column', gap: 7, padding: '16px 22px', minWidth: 312,
			border: `1px solid color-mix(in oklch, ${col} 40%, var(--line))`, borderRadius: 14,
			background: 'var(--panel)',
		}}>
			<div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 30, color: C.text }}>
				<span style={{ fontSize: 24 }}>{status}</span>{name}
			</div>
			<div className="sans" style={{ fontSize: 20, color: col }}>{noteText}</div>
		</Appear>
	);
}

// ── Stat: number-over-label card (for the outro) ──
function Stat({ num, label, at = 0 }) {
	return (
		<Appear at={at} dur={0.45} y={12} className="sans" style={{
			display: 'flex', flexDirection: 'column', alignItems: 'center', minWidth: 124,
			padding: '14px 18px', border: '1px solid var(--line)', borderRadius: 12, background: 'var(--panel)',
		}}>
			<span style={{ fontSize: 38, fontWeight: 700, color: C.text }}>{num}</span>
			<span style={{ fontSize: 18, color: C.dim, marginTop: 4 }}>{label}</span>
		</Appear>
	);
}

// ── Counter: integer that eases up to `to` ──
function Counter({ to, at = 0, dur = 0.9 }) {
	const { localTime } = useSprite();
	const t = Easing.easeOutCubic(clamp((localTime - at) / dur, 0, 1));
	return <span>{Math.round(to * t)}</span>;
}

// ── Backdrop: drifting brand glow + grain + vignette ──
function Backdrop() {
	const t = useTime();
	const gx = 50 + Math.sin(t * 0.25) * 8;
	const gy = 38 + Math.cos(t * 0.2) * 6;
	const gx2 = 68 + Math.cos(t * 0.18) * 7;
	return (
		<div style={{ position: 'absolute', inset: 0, background: C.bd, overflow: 'hidden' }}>
			<div style={{ position: 'absolute', inset: '-25%', background: `radial-gradient(38% 38% at ${gx}% ${gy}%, color-mix(in oklch, var(--accent) 24%, transparent), transparent 70%)`, filter: 'blur(10px)' }} />
			<div style={{ position: 'absolute', inset: '-25%', background: `radial-gradient(34% 34% at ${gx2}% 74%, color-mix(in oklch, var(--accent2) 16%, transparent), transparent 70%)` }} />
			<div className="grain" />
			<div style={{ position: 'absolute', inset: 0, background: 'radial-gradient(125% 125% at 50% 48%, transparent 52%, rgba(0,0,0,0.5))' }} />
		</div>
	);
}

// ── Terminal window chrome (persistent across scenes) ──
function TrafficLights() {
	const dot = (c) => <span style={{ width: 15, height: 15, borderRadius: 99, background: c, display: 'inline-block' }} />;
	return <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>{dot('#ff5f57')}{dot('#febc2e')}{dot('#28c840')}</div>;
}
function TitleBar() {
	const t = useTime();
	const labels = [
		[0, 'boss-agent-cli — zsh'], [4.5, '职位发现 · boss search'], [11, '能力真源 · boss schema'],
		[16.5, '合规护栏 · low-risk mode'], [21.5, 'AI 增强 · 多平台'], [26.5, 'boss-agent-cli — zsh'],
	];
	let label = labels[0][1];
	for (const [s, l] of labels) if (t >= s) label = l;
	return (
		<div style={{ height: 62, display: 'flex', alignItems: 'center', padding: '0 24px', borderBottom: '1px solid var(--line)', background: 'var(--term-top)', position: 'relative', flexShrink: 0 }}>
			<TrafficLights />
			<div className="mono" style={{ position: 'absolute', left: 0, right: 0, textAlign: 'center', color: C.dim, fontSize: 20, pointerEvents: 'none' }}>{label}</div>
		</div>
	);
}
function TerminalWindow({ children }) {
	const t = useTime();
	const zoom = interpolate([0, 31], [1.0, 1.022], Easing.easeInOutSine)(t);
	const drift = Math.sin(t * 0.3) * 4;
	return (
		<div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
			<div style={{
				width: 1680, height: 912, transform: `scale(${zoom}) translateY(${drift}px)`,
				background: 'var(--term)', borderRadius: 16, border: '1px solid var(--line)',
				boxShadow: '0 40px 120px rgba(0,0,0,0.55), inset 0 0 0 1px rgba(255,255,255,0.02)',
				overflow: 'hidden', display: 'flex', flexDirection: 'column',
			}}>
				<TitleBar />
				<div style={{ position: 'relative', flex: 1, overflow: 'hidden' }}>
					<div style={{ position: 'absolute', top: 44, left: 56, right: 56, bottom: 44 }}>
						{children}
					</div>
					<div className="scan" style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }} />
				</div>
			</div>
		</div>
	);
}

Object.assign(window, {
	C, MONO, SANS,
	Appear, Shot, Caret, Typer, Prompt, CmdLine,
	J, JLine, note, Caption, Chip, AiRow, PlatformPill, Stat, Counter,
	Backdrop, TrafficLights, TitleBar, TerminalWindow,
});
