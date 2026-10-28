"""Generate actual outputs; never substitute prewritten model traces."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from content_planner.agent import run_agent
from content_planner.domain import parse_time
from content_planner.model import DeepSeekModel, MockModel
from content_planner.storage import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["mock", "deepseek"], default="mock")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    out_dir = args.out or ROOT / "examples" / args.model
    out_dir.mkdir(parents=True, exist_ok=True)
    # Fresh database on each evaluation, preserving earlier runs in their own directory.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    store = Store(ROOT / ".runtime" / f"evaluation-{args.model}-{stamp}.sqlite3")
    summary = {"model_mode": args.model, "recorded_at_utc": datetime.now(timezone.utc).isoformat(), "clock": "2026-10-28T10:00:00+08:00", "cases": []}
    cases = [("normal", "normal", {}, "completed"), ("replan", "replan", {}, "completed"), ("missing", "missing", {}, "needs_input"), ("tool_failure", "tool_failure", {"save_plan": "always"}, "incomplete"), ("resume", "tool_failure", {}, "completed"), ("duplicate", "normal", {}, "completed"), ("impossible", "impossible", {}, "incomplete"), ("deadline", "deadline", {}, "completed"), ("no_assets", "no_assets", {}, "needs_input")]
    try:
        for case, input_name, faults, expected in cases:
            raw = json.loads((ROOT / "examples" / "inputs" / (input_name + ".json")).read_text(encoding="utf-8"))
            model = DeepSeekModel() if args.model == "deepseek" else MockModel()
            actual = run_agent(raw, model, store, parse_time(summary["clock"]), faults=faults)
            (out_dir / f"{case}.json").write_text(json.dumps(actual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            quotes = [e["result"] for e in actual["trace"] if e["type"] == "tool_result" and e["tool"] == "estimate_plan"]
            row = {"case": case, "input": f"examples/inputs/{input_name}.json", "output": f"{case}.json", "faults": faults, "expected_status": expected, "actual_status": actual["status"], "passed": actual["status"] == expected, "total_cost": actual["total_cost"], "saved": actual["saved"], "replayed": actual["replayed"], "quote_costs": [q.get("total_cost") for q in quotes], "model_usage": actual["model"]["usage_this_run"]}
            if case == "replan":
                row["observed_replanning"] = len(quotes) >= 2 and any(not q.get("feasible", True) for q in quotes[:-1]) and quotes[-1].get("feasible", False)
                row["passed"] &= row["observed_replanning"]
            if case == "duplicate":
                row["passed"] &= actual["replayed"] and not any(row["model_usage"].values())
            if case == "tool_failure":
                row["passed"] &= not actual["saved"] and bool(actual["partial_results"]["scripts"])
            if case == "resume":
                row["passed"] &= actual["run_number"] == 2 and len([e for e in actual["trace"] if e["type"] == "model_action" and e["tool"] == "generate_script"]) == 1
            summary["cases"].append(row)
            (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(row, ensure_ascii=False), flush=True)
        summary["saved_plan_count"] = store.connection.execute("SELECT COUNT(*) FROM plans").fetchone()[0]
        summary["passed"] = all(c["passed"] for c in summary["cases"]) and summary["saved_plan_count"] == 4
        (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        store.close()
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
