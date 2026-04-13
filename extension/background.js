/**
 * BOSS Agent Bridge — Service Worker (background script).
 *
 * Connects to boss-agent-cli daemon via WebSocket, receives commands,
 * dispatches them to Chrome APIs (debugger/tabs/cookies), returns results.
 */

const DAEMON_PORT = 19826;
const DAEMON_WS_URL = `ws://127.0.0.1:${DAEMON_PORT}/ext`;
const DAEMON_PING_URL = `http://127.0.0.1:${DAEMON_PORT}/ping`;
const WINDOW_IDLE_TIMEOUT = 30000;

let ws = null;
let reconnectTimer = null;
let reconnectAttempts = 0;
const MAX_EAGER_ATTEMPTS = 6;
const attached = new Set();

// ── Automation window ────────────────────────────────────────────────

let automationWindowId = null;
let idleTimer = null;

function resetIdleTimer() {
	if (idleTimer) clearTimeout(idleTimer);
	idleTimer = setTimeout(async () => {
		if (automationWindowId) {
			try { await chrome.windows.remove(automationWindowId); } catch {}
			automationWindowId = null;
		}
	}, WINDOW_IDLE_TIMEOUT);
}

async function getAutomationWindow() {
	if (automationWindowId) {
		try {
			await chrome.windows.get(automationWindowId);
			return automationWindowId;
		} catch {
			automationWindowId = null;
		}
	}
	const win = await chrome.windows.create({
		url: 'data:text/html,<html></html>',
		focused: false,
		width: 1280,
		height: 900,
		type: 'normal',
	});
	automationWindowId = win.id;
	resetIdleTimer();
	await new Promise(r => setTimeout(r, 200));
	return automationWindowId;
}

chrome.windows.onRemoved.addListener((windowId) => {
	if (windowId === automationWindowId) {
		automationWindowId = null;
		if (idleTimer) clearTimeout(idleTimer);
	}
});

// ── CDP via chrome.debugger ──────────────────────────────────────────

async function ensureAttached(tabId) {
	const tab = await chrome.tabs.get(tabId);
	if (!tab.url?.startsWith('http')) {
		attached.delete(tabId);
		throw new Error(`Cannot debug tab ${tabId}: URL is ${tab.url}`);
	}

	if (attached.has(tabId)) {
		try {
			await chrome.debugger.sendCommand({ tabId }, 'Runtime.evaluate', {
				expression: '1', returnByValue: true,
			});
			return;
		} catch {
			attached.delete(tabId);
		}
	}

	try {
		await chrome.debugger.attach({ tabId }, '1.3');
	} catch (e) {
		if (e.message?.includes('Another debugger')) {
			try { await chrome.debugger.detach({ tabId }); } catch {}
			await chrome.debugger.attach({ tabId }, '1.3');
		} else {
			throw new Error(`attach failed: ${e.message}`);
		}
	}
	attached.add(tabId);

	try { await chrome.debugger.sendCommand({ tabId }, 'Runtime.enable'); } catch {}
	try {
		await chrome.debugger.sendCommand({ tabId }, 'Debugger.enable');
		await chrome.debugger.sendCommand({ tabId }, 'Debugger.setBreakpointsActive', { active: false });
	} catch {}
}

async function evaluate(tabId, code) {
	await ensureAttached(tabId);
	const result = await chrome.debugger.sendCommand({ tabId }, 'Runtime.evaluate', {
		expression: code,
		returnByValue: true,
		awaitPromise: true,
	});
	if (result.exceptionDetails) {
		throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text || 'Eval error');
	}
	return result.result?.value;
}

// ── Tab management ───────────────────────────────────────────────────

async function resolveTabId(tabId, workspace) {
	if (tabId !== undefined) {
		try {
			const tab = await chrome.tabs.get(tabId);
			if (tab.url?.startsWith('http')) return tabId;
		} catch {}
	}

	// workspace="boss" 时，优先找已有的 zhipin tab（用户正常浏览的页面）
	if (workspace === 'boss') {
		const zhipinTabs = await chrome.tabs.query({ url: '*://*.zhipin.com/*' });
		if (zhipinTabs.length > 0) return zhipinTabs[0].id;
	}

	const windowId = await getAutomationWindow();
	const tabs = await chrome.tabs.query({ windowId });
	const good = tabs.find(t => t.url?.startsWith('http') || t.url?.startsWith('data:'));
	if (good?.id) return good.id;
	const newTab = await chrome.tabs.create({ windowId, url: 'data:text/html,<html></html>', active: true });
	return newTab.id;
}

// ── WebSocket connection ─────────────────────────────────────────────

async function connect() {
	if (ws?.readyState === WebSocket.OPEN || ws?.readyState === WebSocket.CONNECTING) return;

	try {
		const res = await fetch(DAEMON_PING_URL, { signal: AbortSignal.timeout(1000) });
		if (!res.ok) return;
	} catch {
		return;
	}

	try {
		ws = new WebSocket(DAEMON_WS_URL);
	} catch {
		scheduleReconnect();
		return;
	}

	ws.onopen = () => {
		console.log('[boss-bridge] Connected to daemon');
		reconnectAttempts = 0;
		if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
		ws?.send(JSON.stringify({ type: 'hello', version: chrome.runtime.getManifest().version }));
	};

	ws.onmessage = async (event) => {
		try {
			const cmd = JSON.parse(event.data);
			const result = await handleCommand(cmd);
			ws?.send(JSON.stringify(result));
		} catch (err) {
			console.error('[boss-bridge] Command error:', err);
		}
	};

	ws.onclose = () => {
		ws = null;
		scheduleReconnect();
	};

	ws.onerror = () => ws?.close();
}

function scheduleReconnect() {
	if (reconnectTimer) return;
	reconnectAttempts++;
	if (reconnectAttempts > MAX_EAGER_ATTEMPTS) return;
	const delay = Math.min(2000 * Math.pow(2, reconnectAttempts - 1), 60000);
	reconnectTimer = setTimeout(() => {
		reconnectTimer = null;
		connect();
	}, delay);
}

// ── Command dispatcher ───────────────────────────────────────────────

async function handleCommand(cmd) {
	resetIdleTimer();
	try {
		switch (cmd.action) {
			case 'exec': return await handleExec(cmd);
			case 'navigate': return await handleNavigate(cmd);
			case 'cookies': return await handleCookies(cmd);
			case 'close-window': return await handleCloseWindow(cmd);
			default: return { id: cmd.id, ok: false, error: `Unknown action: ${cmd.action}` };
		}
	} catch (err) {
		return { id: cmd.id, ok: false, error: err.message || String(err) };
	}
}

async function handleExec(cmd) {
	if (!cmd.code) return { id: cmd.id, ok: false, error: 'Missing code' };
	const tabId = await resolveTabId(cmd.tabId, cmd.workspace);
	const data = await evaluate(tabId, cmd.code);
	return { id: cmd.id, ok: true, data };
}

async function handleNavigate(cmd) {
	if (!cmd.url) return { id: cmd.id, ok: false, error: 'Missing url' };
	if (!cmd.url.startsWith('http')) return { id: cmd.id, ok: false, error: 'Only http(s) allowed' };

	const tabId = await resolveTabId(cmd.tabId, cmd.workspace);
	await chrome.tabs.update(tabId, { url: cmd.url });

	// 等待导航完成
	await new Promise((resolve) => {
		let done = false;
		const finish = () => { if (!done) { done = true; chrome.tabs.onUpdated.removeListener(listener); resolve(); } };
		const listener = (id, info) => { if (id === tabId && info.status === 'complete') finish(); };
		chrome.tabs.onUpdated.addListener(listener);
		setTimeout(finish, 15000);
	});

	const tab = await chrome.tabs.get(tabId);
	return { id: cmd.id, ok: true, data: { title: tab.title, url: tab.url, tabId } };
}

async function handleCookies(cmd) {
	if (!cmd.domain) return { id: cmd.id, ok: false, error: 'Missing domain' };
	const cookies = await chrome.cookies.getAll({ domain: cmd.domain });
	const data = cookies.map(c => ({
		name: c.name, value: c.value, domain: c.domain,
		path: c.path, secure: c.secure, httpOnly: c.httpOnly,
	}));
	return { id: cmd.id, ok: true, data };
}

async function handleCloseWindow(cmd) {
	if (automationWindowId) {
		try { await chrome.windows.remove(automationWindowId); } catch {}
		automationWindowId = null;
	}
	return { id: cmd.id, ok: true, data: { closed: true } };
}

// ── Lifecycle ────────────────────────────────────────────────────────

let initialized = false;

function initialize() {
	if (initialized) return;
	initialized = true;
	chrome.alarms.create('keepalive', { periodInMinutes: 0.4 });
	chrome.tabs.onRemoved.addListener((tabId) => attached.delete(tabId));
	chrome.debugger.onDetach.addListener((source) => { if (source.tabId) attached.delete(source.tabId); });
	connect();
	console.log('[boss-bridge] Extension initialized');
}

chrome.runtime.onInstalled.addListener(initialize);
chrome.runtime.onStartup.addListener(initialize);
chrome.alarms.onAlarm.addListener((alarm) => { if (alarm.name === 'keepalive') connect(); });

// Popup status API
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
	if (msg?.type === 'getStatus') {
		sendResponse({ connected: ws?.readyState === WebSocket.OPEN });
	}
	return false;
});
