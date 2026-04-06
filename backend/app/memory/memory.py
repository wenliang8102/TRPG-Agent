"""
增强版记忆模块
实现滑动窗口、智能摘要、消息ID管理和工具调用支持
"""

from __future__ import annotations

from typing import List, Optional, Dict, Any
from datetime import datetime
from uuid import uuid4

from langchain_core.messages import AnyMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.messages import BaseMessage


class EnhancedMemory:
    """增强版记忆管理器

    功能：
    1. 消息ID管理：为每条消息分配唯一ID
    2. 滑动窗口：限制消息数量
    3. 智能摘要：分析消息类型，提取关键信息
    4. 工具调用支持：处理ToolMessage类型
    5. 灵活压缩：可配置的压缩比例
    """

    def __init__(
        self,
        max_messages: int = 20,           # 最大消息数量
        min_messages: int = 5,            # 最少保留消息
        compression_threshold: int = 15,  # 触发压缩的阈值
        summary_ratio: float = 0.3        # 摘要保留比例（0.1-0.5）
    ):
        self.max_messages = max_messages
        self.min_messages = min_messages
        self.compression_threshold = compression_threshold
        self.summary_ratio = max(0.1, min(0.5, summary_ratio))  # 限制在0.1-0.5之间

        # 消息ID存储
        self._message_ids: Dict[int, str] = {}
        self._id_to_message: Dict[str, AnyMessage] = {}

    def process_messages(self, messages: List[AnyMessage]) -> List[AnyMessage]:
        """处理消息列表，返回处理后的消息

        如果消息数量超过阈值，进行压缩
        """
        # 为每条消息分配ID
        self._assign_message_ids(messages)

        if len(messages) <= self.compression_threshold:
            return messages

        # 计算动态保留数量
        retain_count = max(
            self.min_messages,
            int(len(messages) * self.summary_ratio)
        )
        retain_count = min(retain_count, self.max_messages)

        # 需要压缩：保留最新消息 + 添加摘要
        recent_messages = messages[-retain_count:]  # 动态保留数量
        old_messages = messages[:-retain_count]

        if old_messages:
            summary = self._create_enhanced_summary(old_messages)
            return [summary] + recent_messages
        else:
            return recent_messages

    def _assign_message_ids(self, messages: List[AnyMessage]) -> None:
        """为每条消息分配唯一ID"""
        for i, msg in enumerate(messages):
            if not hasattr(msg, 'id') or not msg.id:
                msg_id = str(uuid4())
                msg.id = msg_id
                self._message_ids[i] = msg_id
                self._id_to_message[msg_id] = msg

    def _create_enhanced_summary(self, old_messages: List[AnyMessage]) -> SystemMessage:
        """创建增强的智能摘要"""
        if not old_messages:
            return SystemMessage(content="[之前的对话已压缩]")

        # 消息类型分析
        type_stats = self._analyze_message_types(old_messages)

        # 关键信息提取
        key_info = self._extract_key_info(old_messages)

        # 工具调用统计
        tool_stats = self._analyze_tool_calls(old_messages)

        # 生成结构化摘要
        summary_text = self._build_structured_summary(type_stats, key_info, tool_stats, old_messages)

        return SystemMessage(content=summary_text)

    def _analyze_message_types(self, messages: List[AnyMessage]) -> Dict[str, int]:
        """分析消息类型分布"""
        stats = {
            'human': 0,
            'ai': 0,
            'tool': 0,
            'system': 0
        }

        for msg in messages:
            if isinstance(msg, HumanMessage):
                stats['human'] += 1
            elif isinstance(msg, AIMessage):
                stats['ai'] += 1
            elif isinstance(msg, ToolMessage):
                stats['tool'] += 1
            elif isinstance(msg, SystemMessage):
                stats['system'] += 1

        return stats

    def _extract_key_info(self, messages: List[AnyMessage]) -> List[str]:
        """提取关键信息"""
        key_info = []

        for msg in messages:
            if isinstance(msg, HumanMessage):
                content = str(msg.content)

                # 识别重要表达
                important_keywords = ['决定', '选择', '要', '想', '必须', '重要', '记住']
                if any(keyword in content for keyword in important_keywords):
                    # 提取关键句子
                    if len(content) > 50:
                        content = content[:47] + "..."
                    key_info.append(f"用户: {content}")

                # 提取偏好信息
                preference_keywords = ['喜欢', '讨厌', '偏好', '习惯', '风格']
                if any(keyword in content for keyword in preference_keywords):
                    if len(content) > 50:
                        content = content[:47] + "..."
                    key_info.append(f"偏好: {content}")

        return key_info[:5]  # 最多保留5条关键信息

    def _analyze_tool_calls(self, messages: List[AnyMessage]) -> Dict[str, Any]:
        """分析工具调用"""
        tool_calls = []
        tool_responses = 0

        for msg in messages:
            if isinstance(msg, AIMessage) and hasattr(msg, 'tool_calls') and msg.tool_calls:
                for tool_call in msg.tool_calls:
                    tool_calls.append(tool_call.get('name', 'unknown_tool'))
            elif isinstance(msg, ToolMessage):
                tool_responses += 1

        return {
            'tool_call_count': len(tool_calls),
            'unique_tools': list(set(tool_calls)),
            'tool_responses': tool_responses
        }

    def _build_structured_summary(self, type_stats: Dict[str, int], key_info: List[str],
                                 tool_stats: Dict[str, Any], messages: List[AnyMessage]) -> str:
        """构建结构化摘要"""
        summary_parts = []

        # 1. 基本统计
        summary_parts.append(f"[对话摘要] 之前的对话包含:")
        summary_parts.append(f"  - {type_stats['human']} 条用户消息")
        summary_parts.append(f"  - {type_stats['ai']} 条AI回复")

        if type_stats['tool'] > 0:
            summary_parts.append(f"  - {type_stats['tool']} 条工具消息")

        # 2. 工具调用信息
        if tool_stats['tool_call_count'] > 0:
            summary_parts.append(f"  - {tool_stats['tool_call_count']} 次工具调用")
            if tool_stats['unique_tools']:
                tools_str = ", ".join(tool_stats['unique_tools'][:3])  # 最多显示3个工具
                summary_parts.append(f"    工具: {tools_str}")

        # 3. 关键信息
        if key_info:
            summary_parts.append(f"\n关键信息:")
            for info in key_info[:3]:  # 最多显示3条
                summary_parts.append(f"  - {info}")

        # 4. 时间信息
        if messages:
            summary_parts.append(f"\n时间跨度: {len(messages)} 轮对话")

        return "\n".join(summary_parts)

    def get_stats(self, original_messages: List[AnyMessage], processed_messages: List[AnyMessage]) -> Dict[str, Any]:
        """获取处理统计信息"""
        return {
            'original_count': len(original_messages),
            'processed_count': len(processed_messages),
            'reduction_ratio': 1 - len(processed_messages) / max(1, len(original_messages)),
            'was_compressed': len(processed_messages) < len(original_messages),
            'message_ids_count': len(self._message_ids),
            'compression_ratio': self.summary_ratio
        }

    def get_message_by_id(self, message_id: str) -> Optional[AnyMessage]:
        """通过ID获取消息"""
        return self._id_to_message.get(message_id)

    def get_message_id(self, message_index: int) -> Optional[str]:
        """通过索引获取消息ID"""
        return self._message_ids.get(message_index)

    def clear_message_cache(self) -> None:
        """清除消息缓存"""
        self._message_ids.clear()
        self._id_to_message.clear()


class SimpleMemory:
    """简单记忆管理器（EnhancedMemory的简化版本）

    提供基本的滑动窗口和压缩功能，保持接口简单
    """

    def __init__(
        self,
        max_messages: int = 20,           # 最大消息数量
        min_messages: int = 5,            # 最少保留消息
        compression_threshold: int = 15   # 触发压缩的阈值
    ):
        self.max_messages = max_messages
        self.min_messages = min_messages
        self.compression_threshold = compression_threshold

    def process_messages(self, messages: List[AnyMessage]) -> List[AnyMessage]:
        """处理消息列表，返回处理后的消息

        如果消息数量超过阈值，进行简单压缩
        """
        if len(messages) <= self.compression_threshold:
            return messages

        # 简单压缩：保留最新消息 + 添加简单摘要
        retain_count = max(self.min_messages, len(messages) // 3)  # 保留1/3的消息
        retain_count = min(retain_count, self.max_messages)

        recent_messages = messages[-retain_count:]
        old_messages = messages[:-retain_count]

        if old_messages:
            # 创建简单摘要
            summary = self._create_simple_summary(old_messages)
            return [summary] + recent_messages
        else:
            return recent_messages

    def _create_simple_summary(self, old_messages: List[AnyMessage]) -> SystemMessage:
        """创建简单的摘要"""
        human_count = sum(1 for msg in old_messages if isinstance(msg, HumanMessage))
        ai_count = sum(1 for msg in old_messages if isinstance(msg, AIMessage))

        summary_text = f"[之前的对话已压缩，包含 {human_count} 条用户消息和 {ai_count} 条AI回复]"
        return SystemMessage(content=summary_text)

    def get_stats(self, original_messages: List[AnyMessage], processed_messages: List[AnyMessage]) -> Dict[str, Any]:
        """获取处理统计信息"""
        return {
            'original_count': len(original_messages),
            'processed_count': len(processed_messages),
            'reduction_ratio': 1 - len(processed_messages) / max(1, len(original_messages)),
            'was_compressed': len(processed_messages) < len(original_messages)
        }