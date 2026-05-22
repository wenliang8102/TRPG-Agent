import argparse
import sys
from pathlib import Path

# 复用 backend 内的 trace 工具，避免脚本和应用各维护一套解析逻辑。
sys.path.append(str(Path(__file__).parent / "backend"))

from app.utils.agent_trace import detect_latest_session_id, export_trace_report, load_trace_events


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="导出某个会话的 agent trace 为 markdown 或 JSON 文件")
    parser.add_argument("--session-id", default=None, help="指定要导出的 session id；为空时默认取最近一次 trace")
    parser.add_argument("--trace-dir", default=None, help="trace 根目录，默认读取 backend/logs/agent_traces")
    parser.add_argument("--output", default=None, help="输出文件路径；为空时自动写入 backend/logs/trace_exports")
    parser.add_argument("--format", default="markdown", choices=["markdown", "json"], help="导出格式")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    session_id = args.session_id or detect_latest_session_id(args.trace_dir)
    if not session_id:
        print("未找到任何 trace 文件。请先触发一次对话，让后端生成 trace。")
        return 1

    events = load_trace_events(session_id, trace_dir=args.trace_dir)
    if not events:
        print(f"未找到会话 {session_id} 的 trace 事件。")
        return 1

    output_path = export_trace_report(
        session_id,
        events,
        output_path=args.output,
        output_format=args.format,
    )
    print(f"已导出 {len(events)} 条 trace 到: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())