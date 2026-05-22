"""Windows 下以 psycopg async 兼容的事件循环启动 FastAPI。"""

from __future__ import annotations

import argparse
import asyncio
import sys

import uvicorn

from app.utils.asyncio_policy import configure_windows_selector_event_loop_policy


def _build_loop() -> asyncio.AbstractEventLoop:
    """psycopg async 不支持 Windows Proactor；启动服务时必须在建 loop 前选定 Selector。"""
    configure_windows_selector_event_loop_policy()
    if sys.platform == "win32" and hasattr(asyncio, "SelectorEventLoop"):
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()


def main() -> int:
    """用显式事件循环运行 uvicorn，避免命令行入口抢先创建不兼容 loop。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    config = uvicorn.Config("app.main:app", host=args.host, port=args.port, reload=args.reload)
    server = uvicorn.Server(config)
    loop = _build_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(server.serve())
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
