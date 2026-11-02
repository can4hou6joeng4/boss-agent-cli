"""登录态账号的可区分指纹。

zhipin 的 ``wt2`` 等成功 cookie 是加密 token，没有可直接展示的明文账号
字段；同一账号的 cookie 值稳定、不同账号不同，故用哈希前缀作为账号
指纹——既能区分账号，又不泄露 cookie 本身。浏览器登录用它给多个 context
标注账号，命令层用它把本地操作记录限定在当前账号内。
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from boss_agent_cli.auth.manager import AuthManager


def account_fingerprint(cookie_value: str, *, length: int = 16) -> str:
	"""返回登录态 cookie 值的不可逆指纹；空值返回空串。"""
	if not cookie_value:
		return ""
	return hashlib.sha256(cookie_value.encode("utf-8")).hexdigest()[:length]


def account_scope(auth: "AuthManager") -> str:
	"""返回当前登录态的账号指纹，用于本地操作记录的去重作用域。

	未登录或缺少主 Cookie 时返回空串；此时本地记录退化为只按 ``security_id``
	匹配，与引入账号作用域之前的行为一致。非字符串的返回值（测试替身）同样
	按空串处理，避免把 Mock 对象写进 SQLite。
	"""
	value = auth.account_fingerprint()
	return value if isinstance(value, str) else ""
