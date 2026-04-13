import asyncio
import sys
from pathlib import Path

# 把 backend 目录加到 Python 路径好引入官方 checkpointer
sys.path.append(str(Path(__file__).parent / "backend"))

from app.memory.checkpointer import close_checkpointer, get_checkpointer

async def read_summary(db_path: str):
    """使用 LangGraph 官方异步 Checkpointer 从最近的存档中提取摘要。"""
    try:
        # 这里必须走异步工厂，否则拿到的只是 coroutine 而不是可用的 saver。
        saver = await get_checkpointer(db_path)
        
        # 搜索所有 thread_id
        import sqlite3
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT thread_id FROM checkpoints LIMIT 5")
        rows = cur.fetchall()
        conn.close()

        if not rows:
            print("未在数据库中找到任何记忆记录，对话可能还没落盘。")
            return
            
        print(f"找到 {len(rows)} 个活跃对话 (thread_id)，正在解析最新的一条记录...\n")
        
        for idx, row in enumerate(rows):
            thread_id = row[0]
            config = {"configurable": {"thread_id": thread_id}}
            
            # AsyncSqliteSaver 在主线程事件循环中必须使用异步读取接口。
            checkpoint_tuple = await saver.aget_tuple(config)
            
            if not checkpoint_tuple:
                continue
                
            print(f"[{idx+1}] 会话 ID (thread_id): {thread_id}")
            channel_values = checkpoint_tuple.checkpoint.get("channel_values", {})
            summary = channel_values.get("conversation_summary", "")
            
            if summary:
                print(f"  ✨ [前情提要] (大纲摘要):\n{summary}\n")
            else:
                print("  ⚠️ [前情提要] 尚无 (对话较短，未触发摘要总结)\n")
            
            messages = channel_values.get("messages", [])
            print(f"  💬 [当前流转的真实消息窗口] (共保留 {len(messages)} 条):")
            for m in messages:
                role = m.__class__.__name__.replace("Message", "")
                content = str(m.content)
                print(f"    [{role}]: {content}")
            print("-" * 60)
            
    except Exception as e:
        print(f"读取数据库或解析存档失败: {e}")
    finally:
        await close_checkpointer()

if __name__ == "__main__":
    # 由于 FastAPI 一般在 backend 目录运行，db 的默认相对路径在于 backend/data/... 
    asyncio.run(read_summary("backend/data/context_memory.sqlite3"))