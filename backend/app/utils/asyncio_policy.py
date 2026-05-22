"""asyncio 运行策略兼容配置。"""

from __future__ import annotations

import asyncio
import sys


def configure_windows_selector_event_loop_policy() -> None:
    """psycopg async 在 Windows 上需要 SelectorEventLoop，避免默认 Proactor 连接失败。"""
    if sys.platform != "win32" or not hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        return

    policy = asyncio.get_event_loop_policy()
    selector_policy = asyncio.WindowsSelectorEventLoopPolicy
    if isinstance(policy, selector_policy):
        return

    asyncio.set_event_loop_policy(selector_policy())
