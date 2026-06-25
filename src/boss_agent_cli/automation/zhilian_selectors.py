"""Selector definitions for Zhilian recruiter browser automation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SelectorGroup:
	name: str
	selectors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SelectorHealthReport:
	ok: bool
	reason: str
	missing: tuple[str, ...] = ()
	url: str = ""
	title: str = ""
	screenshot_path: str = ""


@dataclass(frozen=True, slots=True)
class ZhilianRecruiterSelectors:
	chat_urls: tuple[str, ...] = (
		"https://rd6.zhaopin.com/app/im",
		"https://rd6.zhaopin.com/im",
		"https://rd.zhaopin.com/im",
		"https://rd5.zhaopin.com/im",
		"https://rd6.zhaopin.com/chat",
		"https://rd.zhaopin.com/chat",
		"https://rd5.zhaopin.com/chat",
	)
	conversation_item: SelectorGroup = SelectorGroup(
		"conversation_item",
		(
			"[data-zp-automation='conversation-item']",
			"[data-testid='conversation-item']",
			".im-session-item",
			".im-session-item__box",
			"[class*='conversation'][class*='item']",
			"[class*='session'][class*='item']",
			"[class*='chat'][class*='item']",
			".conversation-item",
			".session-item",
			".chat-list-item",
		),
	)
	message_item: SelectorGroup = SelectorGroup(
		"message_item",
		(
			"[data-zp-automation='message-item']",
			"[data-testid='message-item']",
			".im-message",
			".im-message__bubble",
			"[class*='message'][class*='item']",
			"[class*='bubble']",
			".message-item",
			".chat-message",
		),
	)
	message_input: SelectorGroup = SelectorGroup(
		"message_input",
		(
			"[data-zp-automation='message-input']",
			"[data-testid='message-input']",
			"textarea[placeholder='从这里开启对话...']",
			"textarea",
			"[contenteditable='true']",
			"[role='textbox']",
		),
	)
	send_button: SelectorGroup = SelectorGroup(
		"send_button",
		(
			"[data-zp-automation='send-message']",
			"[data-testid='send-message']",
			"button:has-text('发送')",
			"text=发送",
			"[class*='send']",
		),
	)
	exchange_button: SelectorGroup = SelectorGroup(
		"exchange_button",
		(
			"[data-zp-automation='exchange-contact']",
			"[data-testid='exchange-contact']",
			"button:has-text('交换微信')",
			"button:has-text('交换联系方式')",
			"button:has-text('获取联系方式')",
			"text=交换微信",
			"text=交换联系方式",
			"text=获取联系方式",
		),
	)
	safety_keywords: tuple[str, ...] = (
		"验证码",
		"安全验证",
		"账号异常",
		"操作频繁",
		"访问受限",
		"风险验证",
	)
