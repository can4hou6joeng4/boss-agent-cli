"""登录阶段提示必须区分「扫码已被识别」与「凭证已保存」（Issue #488）。

用户看到「检测到登录成功」后立刻收到 ACCOUNT_RISK，会以为 login 成功后又被莫名其妙拒绝。
实际顺序是：识别到 wt2 → 校验页面 → 再校验 → 回首页提取，中间任何一步命中官方验证页
都会停下。所以中间那句只能说「已识别到扫码确认」，不能说「登录成功」。
"""

import inspect

from boss_agent_cli.auth import browser as browser_module


def test_intermediate_message_does_not_claim_login_success() -> None:
	source = inspect.getsource(browser_module)

	# 旧措辞（会在凭证尚未保存时就宣称登录成功）不得回归
	assert "检测到登录成功" not in source
	assert "正在提取凭证" not in source
	# 新措辞明确它只是「识别到扫码确认」
	assert source.count("已识别到扫码确认，正在校验并保存凭证") == 2


def test_both_login_channels_use_the_new_wording() -> None:
	"""CDP 扫码与可见浏览器扫码两条路径都要改，不能只改一条。"""
	login_via_cdp = inspect.getsource(browser_module.login_via_cdp)
	login_via_browser = inspect.getsource(browser_module.login_via_browser)

	assert "已识别到扫码确认" in login_via_cdp
	assert "已识别到扫码确认" in login_via_browser
