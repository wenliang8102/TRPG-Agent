"""
滑动窗口记忆模块
实现基于时间或数量的滑动窗口记忆管理
"""

from __future__ import annotations

from typing import List, Optional, Dict, Any, Deque
from collections import deque
from datetime import datetime, timedelta

from langchain_core.messages import AnyMessage, SystemMessage


class SlidingWindowMemory:
    """滑动窗口记忆管理器

    功能：
    1. 基于消息数量的滑动窗口
    2. 基于时间的滑动窗口
    3. 自动清理过期消息
    4. 支持动态窗口大小调整
    """

    def __init__(
        self,
        window_size: int = 10,           # 窗口大小（消息数量）
        time_window_minutes: int = 60,   # 时间窗口（分钟）
        use_time_window: bool = False    # 是否使用时间窗口
    ):
        self.window_size = window_size
        self.time_window_minutes = time_window_minutes
        self.use_time_window = use_time_window

        # 消息队列（包含时间戳）
        self._messages: Deque[Dict[str, Any]] = deque()

    def add_message(self, message: AnyMessage) -> None:
        """添加消息到滑动窗口"""
        message_data = {
            'message': message,
            'timestamp': datetime.now()
        }

        self._messages.append(message_data)
        self._cleanup()

    def add_messages(self, messages: List[AnyMessage]) -> None:
        """批量添加消息"""
        for message in messages:
            self.add_message(message)

    def get_messages(self) -> List[AnyMessage]:
        """获取当前窗口内的消息"""
        self._cleanup()
        return [msg_data['message'] for msg_data in self._messages]

    def _cleanup(self) -> None:
        """清理过期或超出窗口大小的消息"""
        now = datetime.now()

        if self.use_time_window:
            # 基于时间的清理
            cutoff_time = now - timedelta(minutes=self.time_window_minutes)

            # 移除过期消息
            while self._messages and self._messages[0]['timestamp'] < cutoff_time:
                self._messages.popleft()
        else:
            # 基于数量的清理
            while len(self._messages) > self.window_size:
                self._messages.popleft()

    def resize_window(self, new_size: int) -> None:
        """调整窗口大小"""
        self.window_size = new_size
        self._cleanup()

    def set_time_window(self, minutes: int) -> None:
        """设置时间窗口大小"""
        self.time_window_minutes = minutes
        self.use_time_window = True
        self._cleanup()

    def set_count_window(self, count: int) -> None:
        """设置数量窗口大小"""
        self.window_size = count
        self.use_time_window = False
        self._cleanup()

    def clear(self) -> None:
        """清空所有消息"""
        self._messages.clear()

    def get_stats(self) -> Dict[str, Any]:
        """获取窗口统计信息"""
        self._cleanup()

        if not self._messages:
            return {
                'message_count': 0,
                'oldest_message': None,
                'newest_message': None,
                'window_type': 'time' if self.use_time_window else 'count'
            }

        return {
            'message_count': len(self._messages),
            'oldest_message': self._messages[0]['timestamp'],
            'newest_message': self._messages[-1]['timestamp'],
            'window_type': 'time' if self.use_time_window else 'count',
            'window_size': self.time_window_minutes if self.use_time_window else self.window_size
        }


class ConversationWindow:
    """对话滑动窗口（针对TRPG场景优化）

    专为TRPG对话设计的滑动窗口，考虑对话的连贯性
    """

    def __init__(
        self,
        max_turns: int = 10,             # 最大对话轮数
        preserve_context: bool = True    # 是否保留重要上下文
    ):
        self.max_turns = max_turns
        self.preserve_context = preserve_context
        self._conversation_turns: Deque[List[AnyMessage]] = deque()

    def add_turn(self, user_message: AnyMessage, ai_response: AnyMessage) -> None:
        """添加一轮对话"""
        turn = [user_message, ai_response]
        self._conversation_turns.append(turn)

        # 维护窗口大小
        while len(self._conversation_turns) > self.max_turns:
            self._conversation_turns.popleft()

    def get_messages(self) -> List[AnyMessage]:
        """获取窗口内的所有消息"""
        messages = []
        for turn in self._conversation_turns:
            messages.extend(turn)
        return messages

    def get_recent_turns(self, n: int) -> List[List[AnyMessage]]:
        """获取最近的n轮对话"""
        turns_list = list(self._conversation_turns)
        return turns_list[-n:] if n <= len(turns_list) else turns_list

    def clear(self) -> None:
        """清空对话窗口"""
        self._conversation_turns.clear()

    def get_turn_count(self) -> int:
        """获取当前对话轮数"""
        return len(self._conversation_turns)