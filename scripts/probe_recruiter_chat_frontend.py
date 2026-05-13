#!/usr/bin/env python3
"""Probe BOSS recruiter chat frontend for sendMessage JS entry points.

Issue can4hou6joeng4/boss-agent-cli#217 — BOSS 招聘者发消息端点已从
``fastReply/sendReplyMsg`` 迁移到 WebSocket + Protobuf 双通道。本脚本通过
CDP Chrome 在招聘者 chat 页注入 WebSocket 监听 + Vuex 探测，定位前端实际
负责发送消息的 JS 入口（chat store action / function），为后续修复方案
（无论 A / A' / B）提供精确的现场信息。

使用前置：
  1. 用 ``boss-chrome`` 启动 CDP Chrome（带远程调试端口）
  2. 在该 Chrome 中扫码登录招聘者账号
  3. 先在 chat 页与任一候选人建立沟通（保证 friend_id 有效）

跑脚本：
  uv run python scripts/probe_recruiter_chat_frontend.py --friend-id 12345
  uv run python scripts/probe_recruiter_chat_frontend.py --dry-run       # 只打印 JS payload

脚本会引导你在浏览器里**手动**输入一条测试消息并点发送——这一步触发的
WebSocket 帧 + JS 调用栈是侦察的核心信号源。完成后 5 秒内自动汇总输出。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

_DEFAULT_CDP_URL = "http://localhost:9222"
_CHAT_URL_TPL = "https://www.zhipin.com/web/chat/index?friendId={friend_id}"

# ─── JS payloads ─────────────────────────────────────────────────────────

# 在导航前注入：劫持 window.WebSocket，记录 url + 每条 send 的 stack trace
JS_WEBSOCKET_SPY = r"""
(() => {
  if (window.__hf_ws_probe__) return;
  window.__hf_ws_probe__ = { calls: [], sends: [] };
  const OriginalWS = window.WebSocket;
  function PatchedWS(url, protocols) {
    const ws = protocols ? new OriginalWS(url, protocols) : new OriginalWS(url);
    window.__hf_ws_probe__.calls.push({
      url: String(url),
      created_at: Date.now(),
    });
    const origSend = ws.send.bind(ws);
    ws.send = function(data) {
      try {
        const stack = (new Error()).stack || '';
        window.__hf_ws_probe__.sends.push({
          url: String(url),
          ts: Date.now(),
          byte_len: data && data.byteLength != null ? data.byteLength : (data ? String(data).length : 0),
          stack: stack.split('\n').slice(0, 8).join('\n'),
        });
      } catch (e) {}
      return origSend(data);
    };
    return ws;
  }
  PatchedWS.prototype = OriginalWS.prototype;
  PatchedWS.CONNECTING = OriginalWS.CONNECTING;
  PatchedWS.OPEN = OriginalWS.OPEN;
  PatchedWS.CLOSING = OriginalWS.CLOSING;
  PatchedWS.CLOSED = OriginalWS.CLOSED;
  window.WebSocket = PatchedWS;
})();
"""

# 全局变量扫描：列出 window 上 chat/message/store 相关的键
JS_GLOBAL_SCAN = r"""
() => {
  const keys = [];
  try {
    for (const k of Object.keys(window)) {
      if (/chat|message|msg|store|app|vue|nuxt|pinia/i.test(k)) keys.push(k);
    }
  } catch (e) {}
  return keys.sort();
}
"""

# Vue / Vuex / Pinia 探测：在 #wrap / #app DOM 节点上找 __vue_app__ / __vue__
JS_VUE_PROBE = r"""
() => {
  const out = [];
  const tryNode = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return;
    if (el.__vue_app__) {
      const app = el.__vue_app__;
      out.push({source: 'vue3-app@' + sel, version: app.version});
      try {
        const props = app._context && app._context.config && app._context.config.globalProperties;
        if (props) {
          out.push({source: 'vue3-globalProperties@' + sel, keys: Object.keys(props)});
          if (props.$store) {
            out.push({
              source: 'vuex-store@' + sel,
              state_keys: Object.keys(props.$store.state || {}),
              action_keys: Object.keys(props.$store._actions || {}),
              module_keys: Object.keys(props.$store._modules?.root?._children || {}),
            });
          }
          if (props.$pinia) {
            out.push({source: 'pinia@' + sel, store_keys: Object.keys(props.$pinia.state.value || {})});
          }
        }
      } catch (e) { out.push({source: 'vue3-error@' + sel, error: String(e)}); }
    }
    if (el.__vue__) {
      const root = el.__vue__;
      out.push({source: 'vue2-root@' + sel, has_store: !!root.$store});
      try {
        if (root.$store) {
          out.push({
            source: 'vuex2-store@' + sel,
            state_keys: Object.keys(root.$store.state || {}),
            action_keys: Object.keys(root.$store._actions || {}),
            module_keys: Object.keys(root.$store._modules?.root?._children || {}),
          });
        }
      } catch (e) { out.push({source: 'vue2-error@' + sel, error: String(e)}); }
    }
  };
  ['#wrap', '#app', 'body', 'main', '[data-v-app]'].forEach(tryNode);
  return out;
}
"""

# WebSocket 监控结果回收
JS_DUMP_WS = r"""
() => window.__hf_ws_probe__ || {calls: [], sends: []}
"""

# ─── Runner ───────────────────────────────────────────────────────────────


def _build_report(*, friend_id: int | None, cdp_url: str) -> dict[str, Any]:
	return {
		"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
		"issue": "https://github.com/can4hou6joeng4/boss-agent-cli/issues/217",
		"cdp_url": cdp_url,
		"friend_id": friend_id,
		"global_chat_keys": [],
		"vue_candidates": [],
		"websocket": {"calls": [], "sends": []},
	}


def _emit_dry_run(report: dict[str, Any]) -> None:
	"""--dry-run 模式：打印 JS payloads + 空 report 骨架，用于审阅。"""
	print("=== dry-run: JS payloads that will be evaluated ===", file=sys.stderr)
	print("\n--- WebSocket spy (add_init_script) ---", file=sys.stderr)
	print(JS_WEBSOCKET_SPY, file=sys.stderr)
	print("\n--- Global var scan ---", file=sys.stderr)
	print(JS_GLOBAL_SCAN, file=sys.stderr)
	print("\n--- Vue/Vuex/Pinia probe ---", file=sys.stderr)
	print(JS_VUE_PROBE, file=sys.stderr)
	print("\n--- WebSocket dump ---", file=sys.stderr)
	print(JS_DUMP_WS, file=sys.stderr)
	print("\n=== report skeleton ===", file=sys.stderr)
	print(json.dumps(report, ensure_ascii=False, indent=2))


_CHAT_URL_MATCH = "/web/chat/index"


def _find_chat_page(context: Any) -> Any:
	"""Return the existing recruiter chat tab in the context, or None."""
	for page in context.pages:
		try:
			if _CHAT_URL_MATCH in page.url:
				return page
		except Exception:
			continue
	return None


def _run_live_probe(report: dict[str, Any], *, friend_id: int, cdp_url: str, wait_seconds: int) -> dict[str, Any]:
	from patchright.sync_api import sync_playwright

	with sync_playwright() as pw:
		try:
			browser = pw.chromium.connect_over_cdp(cdp_url)
		except Exception as exc:
			raise SystemExit(f"无法连接 CDP Chrome ({cdp_url}): {exc}") from exc

		contexts = browser.contexts
		if not contexts:
			raise SystemExit("CDP Chrome 没有可用 context，先在 Chrome 中扫码登录招聘者账号")
		context = contexts[0]

		# Attach 现有 chat tab —— 不开新 tab、不导航（避免触发 BOSS 风控登出）
		page = _find_chat_page(context)
		if page is None:
			raise SystemExit(
				f"未在 CDP 里找到包含 {_CHAT_URL_MATCH} 的 tab。"
				"\n请先在 Chrome 里打开招聘者聊天页（沟通中），再重新跑本脚本。"
			)
		print(f"[probe] attached to existing tab: {page.url}", file=sys.stderr)

		# 在已加载页面里注入 WebSocket spy（劫持后续 ws.send；已有 WS 实例的 send 拦不到）
		try:
			page.evaluate(JS_WEBSOCKET_SPY)
		except Exception as exc:
			print(f"[probe] WARN: WebSocket spy 注入失败: {exc}", file=sys.stderr)

		# Vue/Vuex/Pinia 静态扫描（不需要用户操作就能拿到）
		report["global_chat_keys"] = page.evaluate(JS_GLOBAL_SCAN)
		report["vue_candidates"] = page.evaluate(JS_VUE_PROBE)

		print(
			f"\n>>> 现在请在 Chrome 招聘者 tab 里：\n"
			f"    1. 从左侧联系人列表点中 friendId={friend_id} 的候选人进入会话\n"
			"    2. 在输入框打字 + 点发送按钮（消息内容由你定）\n"
			f"脚本将在 {wait_seconds} 秒后采集 WebSocket 调用栈\n",
			file=sys.stderr,
		)
		page.wait_for_timeout(wait_seconds * 1000)
		report["websocket"] = page.evaluate(JS_DUMP_WS)

	return report


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(
		description="Probe BOSS recruiter chat frontend for sendMessage JS entry points",
	)
	parser.add_argument("--cdp-url", default=_DEFAULT_CDP_URL, help=f"CDP debug URL (default: {_DEFAULT_CDP_URL})")
	parser.add_argument("--friend-id", type=int, help="已沟通过的候选人 friendId（非 dry-run 必填）")
	parser.add_argument("--output", help="JSON 输出文件路径（默认 stdout）")
	parser.add_argument("--wait", type=int, default=15, help="等待用户手动发送消息的秒数（默认 15）")
	parser.add_argument("--dry-run", action="store_true", help="只打印 JS payload，不连 CDP")
	args = parser.parse_args(argv)

	report = _build_report(friend_id=args.friend_id, cdp_url=args.cdp_url)

	if args.dry_run:
		_emit_dry_run(report)
		return 0

	if not args.friend_id:
		parser.error("--friend-id 在非 dry-run 模式下必填")

	report = _run_live_probe(
		report,
		friend_id=args.friend_id,
		cdp_url=args.cdp_url,
		wait_seconds=args.wait,
	)

	output_text = json.dumps(report, ensure_ascii=False, indent=2)
	if args.output:
		with open(args.output, "w", encoding="utf-8") as fp:
			fp.write(output_text)
		print(f"报告已写入 {args.output}", file=sys.stderr)
	else:
		print(output_text)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
