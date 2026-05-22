"""工具注册兼容入口。

历史代码曾从这里导入所有具体工具；当前运行路径统一使用
``app.services.tools`` 及各工具模块。这里仅保留图构建可能需要的注册入口，
避免继续把旧模块当作全量工具聚合层维护。
"""

from app.services.tools import get_tool_profile, get_tools

__all__ = ["get_tool_profile", "get_tools"]
