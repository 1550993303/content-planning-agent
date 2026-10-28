"""Synthetic business tools. Validation is independent of model claims."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from importlib.resources import files

from .domain import digest, parse_time


def load_catalog() -> dict:
    return json.loads(files("content_planner").joinpath("data/catalog.json").read_text(encoding="utf-8"))


def schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {"type": "object", "properties": {**properties, "reason": {"type": "string", "description": "一句话可公开的行动依据，不含内部推理"}}, "required": [*required, "reason"], "additionalProperties": False}}}


TOOL_SCHEMAS = [
    schema("search_assets", "检索合成素材。可改写查询；返回的素材才可用于估算。费用和耗时以估算工具为准。", {"query": {"type": "string"}}, ["query"]),
    schema("estimate_plan", "估算素材与脚本的总费用、串行耗时，检查预算和截止时间。可在观察结果后替换素材或脚本档位重新估算。", {"asset_ids": {"type": "array", "items": {"type": "string"}}, "script_tier": {"type": "string", "enum": ["premium", "basic"]}}, ["asset_ids", "script_tier"]),
    schema("generate_script", "根据已通过的估算，用本地模板生成带来源的脚本；返回 script_id。不会真实购买或发布。", {"quote_id": {"type": "string"}}, ["quote_id"]),
    schema("save_plan", "校验估算与脚本后保存本地方案，幂等。只有 ok=true 才表示保存成功。", {"quote_id": {"type": "string"}, "script_id": {"type": "string"}}, ["quote_id", "script_id"]),
    schema("finish", "完成或明确停止。未保存不得声明 completed。资料缺失可 needs_input；无可行方案或工具失败应 incomplete。", {"status": {"type": "string", "enum": ["completed", "needs_input", "incomplete"]}, "message": {"type": "string"}}, ["status", "message"]),
]


def validate_args(name: str, args: dict):
    tool = next((s["function"] for s in TOOL_SCHEMAS if s["function"]["name"] == name), None)
    if not tool or not isinstance(args, dict):
        raise ValueError("未知工具或参数不是对象")
    spec = tool["parameters"]
    if set(args) != set(spec["required"]):
        raise ValueError("参数缺失或包含未声明字段")
    for key, prop in spec["properties"].items():
        val = args[key]
        if prop["type"] == "string" and (not isinstance(val, str) or not val.strip() or len(val) > 1000):
            raise ValueError(f"{key} 必须为非空字符串，最多1000字")
        if "enum" in prop and val not in prop["enum"]:
            raise ValueError(f"{key} 不在可选值中")
        if prop["type"] == "array" and (not isinstance(val, list) or not 1 <= len(val) <= 5 or any(not isinstance(x, str) for x in val) or len(set(val)) != len(val)):
            raise ValueError(f"{key} 必须为1–5个不重复字符串")


class Tools:
    def __init__(self, request, state, store, now: datetime, faults=None, catalog=None):
        self.request, self.state, self.store, self.now = request, state, store, now
        self.faults = dict(faults or {})
        self.catalog = catalog or load_catalog()

    def execute(self, name: str, args: dict) -> dict:
        validate_args(name, args)
        values = {k: v for k, v in args.items() if k != "reason"}
        cache_key = digest([name, values, self.now.isoformat() if name == "estimate_plan" else None])
        if name != "finish" and cache_key in self.state["cache"]:
            return {**self.state["cache"][cache_key], "cached": True}
        fault = self.faults.get(name)
        if fault:
            if fault != "always":
                self.faults[name] = int(fault) - 1
            return {"ok": False, "error": "injected_tool_failure", "retryable": fault != "always", "message": f"测试注入：{name} 失败，未产生新产物"}
        result = getattr(self, name)(**values)
        if result.get("ok") and name != "finish":
            self.state["cache"][cache_key] = result
        return result

    def search_assets(self, query: str) -> dict:
        query = query.casefold()
        assets = [a for a in self.catalog["assets"] if any(t.casefold() in query or query in t.casefold() for t in a["tags"])]
        self.state["discovered"].update({a["id"]: a for a in assets})
        return {"ok": True, "assets": [{k: a[k] for k in ("id", "title", "quality", "facts")} for a in assets], "script_tiers": list(self.catalog["script_tiers"]), "message": "费用与耗时需调用 estimate_plan；无结果可以改写查询或请求补充"}

    def estimate_plan(self, asset_ids: list[str], script_tier: str) -> dict:
        if any(a not in self.state["discovered"] for a in asset_ids):
            return {"ok": False, "error": "unknown_asset", "message": "必须先检索到全部素材"}
        assets = [self.state["discovered"][a] for a in asset_ids]
        tier = self.catalog["script_tiers"][script_tier]
        cost = sum(a["fee"] for a in assets) + tier["fee"]
        minutes = sum(a["minutes"] for a in assets) + tier["minutes"]
        finish = self.now + timedelta(minutes=minutes)
        deadline = parse_time(self.request["deadline"])
        quote = {"asset_ids": sorted(asset_ids), "script_tier": script_tier, "total_cost": cost, "currency": "simulated_units", "total_minutes": minutes, "estimated_finish": finish.isoformat(), "budget_gap": float(max(Decimal(0), Decimal(str(cost)) - Decimal(str(self.request["budget"])))), "deadline_gap_minutes": max(0, (finish - deadline).total_seconds() / 60), "breakdown": [{"step": "prepare_assets", "cost": sum(a["fee"] for a in assets), "minutes": sum(a["minutes"] for a in assets)}, {"step": "generate_script", "cost": tier["fee"], "minutes": tier["minutes"]}]}
        quote["feasible"] = cost <= self.request["budget"] and finish <= deadline
        qid = "Q-" + digest(quote)[:16]
        quote["quote_id"] = qid
        self.state["quotes"][qid] = quote
        return {"ok": True, **quote, "available_script_tiers": list(self.catalog["script_tiers"])}

    def _valid_quote(self, quote_id: str) -> dict | None:
        quote = self.state["quotes"].get(quote_id)
        if not quote or not quote["feasible"]:
            return None
        # Recheck against the current clock, including a later resume.
        if self.now + timedelta(minutes=quote["total_minutes"]) > parse_time(self.request["deadline"]):
            return None
        return quote

    def generate_script(self, quote_id: str) -> dict:
        quote = self._valid_quote(quote_id)
        if not quote:
            return {"ok": False, "error": "infeasible_or_stale_quote", "message": "必须先获得满足当前预算与截止时间的估算"}
        scenes = [{"source_id": a, "visual": self.state["discovered"][a]["title"], "narration": "；".join(self.state["discovered"][a]["facts"])} for a in quote["asset_ids"]]
        script = {"script_id": "S-" + digest([quote_id, self.request["theme"]])[:16], "quote_id": quote_id, "title": self.request["theme"] + "活动脚本", "tier": quote["script_tier"], "scenes": scenes, "call_to_action": "查看活动详情，参与主题互动", "generator": "local_synthetic_template"}
        self.state["scripts"][script["script_id"]] = script
        return {"ok": True, **script}

    def save_plan(self, quote_id: str, script_id: str) -> dict:
        quote = self._valid_quote(quote_id)
        script = self.state["scripts"].get(script_id)
        if not quote or not script or script["quote_id"] != quote_id:
            return {"ok": False, "error": "dependency_not_satisfied", "message": "需要可行估算及其对应的成功脚本，未保存"}
        plan = {"plan_id": "P-" + self.request["request_id"], "request_id": self.request["request_id"], "theme": self.request["theme"], "budget": self.request["budget"], "deadline": self.request["deadline"], "quote": quote, "script": script, "tasks": [{"name": "prepare_assets", "depends_on": [], "status": "completed", "asset_ids": quote["asset_ids"]}, {"name": "generate_script", "depends_on": ["prepare_assets"], "status": "completed", "script_id": script_id}, {"name": "save_plan", "depends_on": ["generate_script"], "status": "completed"}], "execution_scope": "synthetic_planning_only"}
        saved = self.store.save_plan(self.request["request_id"], plan)
        self.state["saved_plan"] = saved
        return {"ok": True, "plan_id": saved["plan_id"], "total_cost": quote["total_cost"], "saved": True, "location": "sqlite:plans/" + self.request["request_id"]}

    def finish(self, status: str, message: str) -> dict:
        if status == "completed" and not self.state["saved_plan"]:
            return {"ok": False, "error": "not_saved", "message": "未成功保存，不能声明完成"}
        self.state["status"] = status
        self.state["message"] = message
        return {"ok": True, "status": status, "message": message}
