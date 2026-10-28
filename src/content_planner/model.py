"""Real tool-call adapter and explicitly offline policy mock."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .domain import canonical


class ModelError(Exception):
    pass


class DeepSeekModel:
    provider = "deepseek"

    def __init__(self, *, timeout=45):
        self.name = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
        self.base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme != "https" or parsed.hostname != "api.deepseek.com" or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ModelError("仅允许 https://api.deepseek.com 的接口地址，避免将密钥发给其他主机")
        self.key = os.environ.get("DEEPSEEK_API_KEY")
        if not self.key:
            raise ModelError("缺少 DEEPSEEK_API_KEY 系统环境变量；离线验证可用 --model mock")
        self.timeout, self.usage = timeout, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def choose(self, messages, tools):
        body = {"model": self.name, "messages": messages, "tools": tools, "tool_choice": "auto", "thinking": {"type": "disabled"}, "temperature": 0, "max_tokens": 1200, "stream": False}
        req = urllib.request.Request(self.base_url + "/chat/completions", data=canonical(body).encode("utf-8"), headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"})
        # Reject redirects: never forward credentials to an unexpected destination.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        try:
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=self.timeout) as response:
                data = json.load(response)
            for key in self.usage:
                self.usage[key] += data.get("usage", {}).get(key, 0)
            msg = data["choices"][0]["message"]
            # No reasoning_content is stored or requested.
            return {"role": "assistant", "content": msg.get("content") or "", "tool_calls": msg.get("tool_calls") or []}
        except urllib.error.HTTPError as exc:
            raise ModelError(f"模型接口返回 HTTP {exc.code}；未记录响应正文或凭证") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise ModelError("模型连接失败或响应无效；可稍后使用同一请求续跑") from None


class MockModel:
    """Rule-based simulator, never evidence of real-model decisions."""
    provider, name = "offline_mock", "rule_based_mock"
    usage = {}

    def choose(self, messages, tools):
        context = json.loads(messages[1]["content"])
        request, state = context["request"], context["progress"]
        observations = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        for result in observations:
            if result.get("ok"):
                if "assets" in result:
                    state["assets"] = result["assets"]
                if "quote_id" in result and "feasible" in result:
                    state["quotes"].append(result)
                if "script_id" in result:
                    state["scripts"].append(result)
                if result.get("saved"):
                    state["saved"] = True
        def action(name, **args):
            return {"role": "assistant", "content": "", "tool_calls": [{"id": f"mock-{len(observations)+1}", "type": "function", "function": {"name": name, "arguments": canonical(args)}}]}
        if state["saved"]:
            return action("finish", status="completed", message="方案已通过约束并保存。", reason="观察到保存成功")
        if observations and not observations[-1].get("ok"):
            last = observations[-1]
            if not last.get("retryable", False):
                return action("finish", status="incomplete", message="工具失败，保留成功结果以便续跑。", reason="工具返回不可重试失败")
            previous = next(m for m in reversed(messages) if m["role"] == "assistant")
            call = previous["tool_calls"][0]["function"]
            return action(call["name"], **{**json.loads(call["arguments"]), "reason": "观察到临时失败，重试该步骤"})
        if not state["assets"]:
            if observations and "assets" in observations[-1]:
                return action("finish", status="needs_input", message="未找到相关素材，请补充素材或主题。", reason="检索返回空结果")
            return action("search_assets", query=request["theme"], reason="先查找可用素材")
        quote = state["quotes"][-1] if state["quotes"] else None
        if not quote:
            asset = next((a for a in state["assets"] if a["quality"] == "premium"), state["assets"][0])
            return action("estimate_plan", asset_ids=[asset["id"]], script_tier="premium", reason="先评估优先品质方案")
        if not quote["feasible"]:
            if quote["script_tier"] == "basic" and all(a["quality"] != "premium" for a in state["assets"] if a["id"] in quote["asset_ids"]):
                return action("finish", status="incomplete", message=f"无可行方案；预算缺口{quote['budget_gap']}，时间缺口{quote['deadline_gap_minutes']}分钟。", reason="基础方案仍未满足约束")
            asset = next((a for a in state["assets"] if a["quality"] == "basic"), state["assets"][0])
            return action("estimate_plan", asset_ids=[asset["id"]], script_tier="basic", reason="观察到预算或时间超限，改评估基础方案")
        script = next((s for s in state["scripts"] if s["quote_id"] == quote["quote_id"]), None)
        if not script:
            return action("generate_script", quote_id=quote["quote_id"], reason="估算可行，生成脚本")
        return action("save_plan", quote_id=quote["quote_id"], script_id=script["script_id"], reason="依赖已完成，保存方案")
