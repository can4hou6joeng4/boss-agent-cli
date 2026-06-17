// app.jsx — assembles the persistent terminal set, sequences the six scenes
// along the timeline, and mounts the Stage.
const { Stage, Sprite, useTime, Backdrop, TerminalWindow } = window;

const SCENES = [
	{ Comp: window.Scene1, start: 0, end: 4.5 },
	{ Comp: window.Scene2, start: 4.5, end: 11 },
	{ Comp: window.Scene3, start: 11, end: 16.5 },
	{ Comp: window.Scene4, start: 16.5, end: 21.5 },
	{ Comp: window.Scene5, start: 21.5, end: 26.5 },
	{ Comp: window.Scene6, start: 26.5, end: 31 },
];

function Root() {
	const t = useTime();
	return (
		<div data-screen-label={`t=${Math.floor(t)}s`} style={{ position: 'absolute', inset: 0 }}>
			<Backdrop />
			<TerminalWindow>
				{SCENES.map(({ Comp, start, end }, i) => (
					<Sprite key={i} start={start} end={end}>
						<Comp />
					</Sprite>
				))}
			</TerminalWindow>
		</div>
	);
}

function App() {
	return (
		<Stage width={1920} height={1080} duration={31} background="var(--bd)" persistKey="boss-promo">
			<Root />
		</Stage>
	);
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
