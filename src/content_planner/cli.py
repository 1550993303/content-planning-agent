from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from .agent import run_agent
from .domain import parse_time
from .model import DeepSeekModel, MockModel, ModelError
from .storage import Store


def main(argv=None):
    parser = argparse.ArgumentParser(description="内容任务规划 Agent：只生成本地合成方案")
    parser.add_argument("input", type=Path, help="输入 JSON 文件")
    parser.add_argument("--model", choices=["mock", "deepseek"], default="mock")
    parser.add_argument("--db", type=Path, default=Path(".runtime/planner.sqlite3"))
    parser.add_argument("--output", type=Path, help="完整输出及 Trace 的 JSON 路径")
    parser.add_argument("--now", help="可选固定评估时钟，必须带时区；默认当前 UTC 时间")
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument("--max-tool-calls", type=int, default=12)
    parser.add_argument("--fault", action="append", default=[], metavar="TOOL:COUNT_OR_ALWAYS", help="仅用于合成业务工具的故障注入，例如 save_plan:always")
    args = parser.parse_args(argv)
    store = None
    try:
        faults = {}
        from .tools import TOOL_SCHEMAS
        names = {s["function"]["name"] for s in TOOL_SCHEMAS} - {"finish"}
        for item in args.fault:
            tool, count = item.split(":", 1)
            if tool not in names or (count != "always" and (not count.isdigit() or int(count) < 1)):
                raise ValueError("无效故障配置")
            faults[tool] = count if count == "always" else int(count)
        raw = json.loads(args.input.read_text(encoding="utf-8-sig"))
        model = DeepSeekModel() if args.model == "deepseek" else MockModel()
        store = Store(args.db)
        result = run_agent(raw, model, store, parse_time(args.now) if args.now else datetime.now(timezone.utc), max_steps=args.max_steps, max_tool_calls=args.max_tool_calls, faults=faults)
        output = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output + "\n", encoding="utf-8")
        print(output)
        return 0 if result["status"] == "completed" else 2
    except (ValueError, OSError, sqlite3.Error, ModelError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        if store:
            store.close()
