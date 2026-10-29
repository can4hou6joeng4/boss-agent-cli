#!/usr/bin/env python3
"""Capture the request identity BOSS 直聘's own pages present.

The httpx channel and the browser channel must look like the same client. This
script records what the real page actually sends (full header values, request
bodies, statuses) so that claim can be checked against evidence instead of
assumed.

使用前置：
  1. 以远程调试端口启动 Chrome，例如
     "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \\
       --remote-debugging-port=9222 \\
       --user-data-dir="$HOME/.boss-agent/cdp-chrome-profile"
  2. 在该 Chrome 中扫码登录 BOSS 直聘

跑脚本：
  uv run python scripts/capture_request_identity.py --query Python --city 101280100
  uv run python scripts/capture_request_identity.py --url "https://www.zhipin.com/web/geek/recommend"

输出是完整原始记录（含 Cookie 头与响应体），只能写进 .gitignore 覆盖的目录，
不要提交，也不要把内容贴进 issue 或 PR。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

_DEFAULT_CDP_URL = "http://localhost:9222"
_DEFAULT_OUTPUT = ".capture/request-identity.json"
_SEARCH_URL_TPL = "https://www.zhipin.com/web/geek/jobs?query={query}&city={city}"


def _find_boss_page(context: Any) -> Any:
	for page in context.pages:
		if "zhipin.com" in (page.url or ""):
			return page
	raise SystemExit("当前 context 里没有 zhipin.com 页面，请先在 Chrome 中打开并登录")


def _serialize_headers(request: Any) -> dict[str, str]:
	"""Return headers without issuing a CDP round trip.

	Calling ``request.all_headers()`` from inside an event handler re-enters the
	connection for every request and stalls the page, which suppresses the very
	XHRs this script is trying to record. Only ``request.headers`` (a snapshot
	already carried by the event) is read here.
	"""
	try:
		return {str(k): str(v) for k, v in (request.headers or {}).items()}
	except Exception as exc:  # pragma: no cover - depends on live CDP state
		return {"__header_read_error__": f"{type(exc).__name__}: {exc}"}


def _capture(
	*, cdp_url: str, target_url: str, settle_seconds: float, max_body_bytes: int
) -> dict[str, Any]:
	try:
		from patchright.sync_api import sync_playwright
	except ImportError as exc:  # pragma: no cover - optional extra
		raise SystemExit(f"需要 patchright：{exc}") from exc

	records: list[dict[str, Any]] = []
	bodies: dict[str, str] = {}

	with sync_playwright() as pw:
		try:
			browser = pw.chromium.connect_over_cdp(cdp_url)
		except Exception as exc:
			raise SystemExit(f"无法连接 CDP Chrome ({cdp_url}): {exc}") from exc

		context = browser.contexts[0] if browser.contexts else browser.new_context()
		page = _find_boss_page(context)

		tracked: list[Any] = []

		def on_request(request: Any) -> None:
			if "/wapi/" in request.url:
				tracked.append(request)
			entry: dict[str, Any] = {
				"kind": "request",
				"url": request.url,
				"method": request.method,
				"resource_type": request.resource_type,
				"headers": _serialize_headers(request),
				"post_data": request.post_data,
				"timestamp": request.timing.get("startTime") if request.timing else None,
			}
			records.append(entry)

		def on_response(response: Any) -> None:
			entry: dict[str, Any] = {
				"kind": "response",
				"url": response.url,
				"status": response.status,
				"headers": dict(response.headers) if response.headers else {},
			}
			records.append(entry)
			if response.request.resource_type not in {"xhr", "fetch"} or response.status != 200:
				return
			try:
				bodies[response.url] = response.text()[:max_body_bytes]
			except Exception as exc:  # pragma: no cover - depends on live CDP state
				bodies[response.url] = f"<read_error {type(exc).__name__}: {exc}>"

		page.on("request", on_request)
		page.on("response", on_response)

		page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
		time.sleep(settle_seconds)

		final_url = page.url

		# 业务请求的完整 header（含浏览器网络栈补的 Accept-Language / Sec-Fetch-*）
		# 只能在事件处理器之外读取。
		full_headers: dict[str, dict[str, str]] = {}
		post_data: dict[str, Any] = {}
		for request in tracked:
			try:
				full_headers[request.url] = dict(request.all_headers())
				post_data[request.url] = request.post_data
			except Exception as exc:  # pragma: no cover - depends on live CDP state
				full_headers[request.url] = {"__header_read_error__": f"{type(exc).__name__}: {exc}"}

	return {
		"cdp_url": cdp_url,
		"requested_url": target_url,
		"final_url": final_url,
		"captured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
		"record_count": len(records),
		"records": records,
		"business_full_headers": full_headers,
		"business_post_data": post_data,
		"xhr_bodies": bodies,
	}


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("--cdp-url", default=_DEFAULT_CDP_URL)
	parser.add_argument("--url", help="直接指定要访问的页面 URL")
	parser.add_argument("--query", default="Python", help="搜索关键词（未指定 --url 时使用）")
	parser.add_argument("--city", default="101280100", help="城市编码（未指定 --url 时使用）")
	parser.add_argument("--settle-seconds", type=float, default=25.0)
	parser.add_argument("--max-body-bytes", type=int, default=200_000)
	parser.add_argument("--output", default=_DEFAULT_OUTPUT)
	args = parser.parse_args(argv)

	target_url = args.url or _SEARCH_URL_TPL.format(query=args.query, city=args.city)
	report = _capture(
		cdp_url=args.cdp_url,
		target_url=target_url,
		settle_seconds=args.settle_seconds,
		max_body_bytes=args.max_body_bytes,
	)

	output_path = Path(args.output)
	output_path.parent.mkdir(parents=True, exist_ok=True)
	output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

	business = [r for r in report["records"] if r["kind"] == "response" and "/wapi/" in r["url"]]
	print(json.dumps({
		"output": str(output_path),
		"final_url": report["final_url"],
		"records": report["record_count"],
		"business_responses": [
			{"url": r["url"].split("?")[0], "status": r["status"]} for r in business[:20]
		],
	}, ensure_ascii=False, indent=2))
	if "verify" in report["final_url"] or "403" in report["final_url"]:
		print("警告：落到了安全验证/限制页", file=sys.stderr)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
