from copy import deepcopy
from datetime import timedelta
import json
import sqlite3

import pytest

from content_planner.agent import run_agent
from content_planner.domain import canonical, parse_time, validate_request
from content_planner.model import MockModel, ModelError, DeepSeekModel
from content_planner.storage import Store
from content_planner.tools import Tools, load_catalog

NOW = parse_time("2026-10-28T10:00:00+08:00")


def request(**overrides):
    return {"request_id": "test", "theme": "校园毕业季", "budget": 20, "deadline": "2026-10-29T18:00:00+08:00", "preferences": "优先精品方案，若超预算可降级", **overrides}


@pytest.fixture
def store(tmp_path):
    obj = Store(tmp_path / "state.sqlite3")
    yield obj
    obj.close()


def results(output, name):
    return [e["result"] for e in output["trace"] if e["type"] == "tool_result" and e["tool"] == name]


def test_normal_completion(store):
    out = run_agent(request(), MockModel(), store, NOW)
    assert out["status"] == "completed" and out["saved"]
    assert out["total_cost"] == 12
    assert out["saved_plan"]["script"]["scenes"][0]["source_id"] == "A01"
    assert out["saved_plan"]["quote"]["total_minutes"] == 120
    assert all(t["status"] == "completed" for t in out["saved_plan"]["tasks"])


def test_over_budget_replans_from_observation(store):
    out = run_agent(request(budget=10), MockModel(), store, NOW)
    estimates = results(out, "estimate_plan")
    assert [q["total_cost"] for q in estimates] == [12, 8]
    assert [q["feasible"] for q in estimates] == [False, True]
    assert out["total_cost"] == 8 and out["status"] == "completed"


def test_deadline_replans(store):
    out = run_agent(request(deadline=(NOW + timedelta(minutes=60)).isoformat()), MockModel(), store, NOW)
    quotes = results(out, "estimate_plan")
    assert quotes[0]["deadline_gap_minutes"] == 60
    assert out["saved_plan"]["quote"]["total_minutes"] == 45


@pytest.mark.parametrize("missing", ["budget", "deadline", "theme"])
def test_missing_input_never_calls_model(store, missing):
    class MustNotRun(MockModel):
        def choose(self, *args):
            pytest.fail("缺必要字段不应调用模型")
    raw = request()
    del raw[missing]
    out = run_agent(raw, MustNotRun(), store, NOW)
    assert out["status"] == "needs_input" and missing in out["missing_fields"]
    assert not out["saved"]


@pytest.mark.parametrize("field,value", [("budget", None), ("budget", ""), ("deadline", None), ("deadline", "")])
def test_empty_required_fields_need_input(store, field, value):
    out = run_agent(request(**{field: value}), MockModel(), store, NOW)
    assert out["status"] == "needs_input" and field in out["missing_fields"]


def test_transient_tool_failure_is_retried_by_policy(store):
    out = run_agent(request(), MockModel(), store, NOW, faults={"generate_script": 1})
    assert [r["ok"] for r in results(out, "generate_script")] == [False, True]
    assert out["status"] == "completed"
    assert len(results(out, "search_assets")) == 1


def test_save_failure_is_not_success_and_resume_keeps_script(store):
    first = run_agent(request(), MockModel(), store, NOW, faults={"save_plan": "always"})
    assert first["status"] == "incomplete" and not first["saved"]
    assert first["partial_results"]["scripts"]
    assert store.connection.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 0
    second = run_agent(request(), MockModel(), store, NOW)
    assert second["status"] == "completed"
    assert len(results(second, "generate_script")) == 1
    assert second["partial_results"]["scripts"] == first["partial_results"]["scripts"]


def test_duplicate_request_is_replayed_without_model_or_second_save(store):
    first = run_agent(request(), MockModel(), store, NOW)
    class MustNotRun(MockModel):
        def choose(self, *args):
            pytest.fail("已完成请求不能再次调用模型")
    second = run_agent(request(), MustNotRun(), store, NOW)
    assert second["replayed"] and second["saved_plan"] == first["saved_plan"]
    assert store.connection.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 1


def test_same_id_different_input_is_rejected(store):
    run_agent(request(), MockModel(), store, NOW)
    with pytest.raises(ValueError, match="request_id"):
        run_agent(request(budget=100), MockModel(), store, NOW)


@pytest.mark.parametrize("overrides", [{"budget": 7}, {"deadline": (NOW + timedelta(minutes=20)).isoformat()}])
def test_no_feasible_plan_returns_gap(store, overrides):
    out = run_agent(request(**overrides), MockModel(), store, NOW)
    assert out["status"] == "incomplete" and not out["saved"]
    quote = out["partial_results"]["quotes"][-1]
    assert quote["budget_gap"] > 0 or quote["deadline_gap_minutes"] > 0


def test_no_assets_needs_input(store):
    out = run_agent(request(theme="火星基地开业"), MockModel(), store, NOW)
    assert out["status"] == "needs_input" and not out["saved"]


def test_model_error_preserves_successful_steps(store):
    class Broken(MockModel):
        def choose(self, messages, tools):
            if any(m["role"] == "tool" for m in messages):
                raise ModelError("模拟模型不可用")
            return super().choose(messages, tools)
    out = run_agent(request(), Broken(), store, NOW)
    assert out["status"] == "incomplete"
    resumed = run_agent(request(), MockModel(), store, NOW)
    assert resumed["status"] == "completed"
    assert len(results(resumed, "search_assets")) == 1


@pytest.mark.parametrize("limits", [{"max_steps": 1}, {"max_tool_calls": 1}])
def test_limits_explicitly_incomplete(store, limits):
    out = run_agent(request(), MockModel(), store, NOW, **limits)
    assert out["status"] == "incomplete" and not out["saved"]
    assert out["trace"][-1]["type"] == "limit_reached"
    assert len([x for x in out["trace"] if x["type"] == "tool_result"]) == 1


def test_untrusted_model_cannot_claim_completed_without_save(store):
    class Liar(MockModel):
        def choose(self, messages, tools):
            return {"role": "assistant", "content": "", "tool_calls": [{"id": "x", "type": "function", "function": {"name": "finish", "arguments": canonical({"status": "completed", "message": "已完成", "reason": "虚假完成测试"})}}]}
    out = run_agent(request(), Liar(), store, NOW, max_steps=2)
    assert out["status"] == "incomplete" and not out["saved"]
    assert all(r["error"] == "not_saved" for r in results(out, "finish"))


def test_invalid_tool_arguments_are_observations(store):
    class Invalid(MockModel):
        def choose(self, messages, tools):
            return {"role": "assistant", "content": "", "tool_calls": [{"id": "x", "function": {"name": "save_plan", "arguments": "{broken"}, "type": "function"}]}
    out = run_agent(request(), Invalid(), store, NOW, max_steps=1)
    assert results(out, "save_plan")[0]["error"] == "invalid_tool_arguments"


def test_plain_text_cannot_bypass_tools_or_loop_forever(store):
    class NoTools(MockModel):
        def choose(self, *args):
            return {"role": "assistant", "content": "已经保存了", "tool_calls": []}
    out = run_agent(request(), NoTools(), store, NOW, max_steps=2)
    assert out["status"] == "incomplete" and not out["saved"]


@pytest.mark.parametrize("budget", [-1, True, float("nan"), float("inf"), "10"])
def test_invalid_budgets(budget):
    with pytest.raises(ValueError):
        validate_request(request(budget=budget))


def test_deadline_requires_timezone():
    with pytest.raises(ValueError, match="时区"):
        validate_request(request(deadline="2026-10-29T18:00:00"))


def test_mutating_catalog_changes_quote_and_outcome(store):
    catalog = deepcopy(load_catalog())
    catalog["assets"][1]["fee"] = 20
    out = run_agent(request(budget=10), MockModel(), store, NOW, catalog=catalog)
    assert out["status"] == "incomplete" and not out["saved"]
    assert results(out, "estimate_plan")[-1]["total_cost"] == 26


def test_dependency_and_budget_guards(store):
    from content_planner.agent import new_state
    state = new_state()
    tools = Tools(request(budget=10), state, store, NOW)
    assert tools.estimate_plan(["invented"], "basic")["error"] == "unknown_asset"
    tools.search_assets("校园毕业季")
    expensive = tools.estimate_plan(["A01"], "premium")
    assert not tools.generate_script(expensive["quote_id"])["ok"]
    assert not tools.save_plan(expensive["quote_id"], "invented")["ok"]
    cheap = tools.estimate_plan(["A02"], "basic")
    script = tools.generate_script(cheap["quote_id"])
    tools.now = NOW + timedelta(days=2)
    assert not tools.save_plan(cheap["quote_id"], script["script_id"])["ok"]


def test_api_key_not_required_in_mock_and_missing_key_error_is_safe(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ModelError, match="DEEPSEEK_API_KEY"):
        DeepSeekModel()


def test_tiny_budget_overrun_is_not_rounded_into_success(store):
    out = run_agent(request(budget=7.9999999), MockModel(), store, NOW)
    assert out["status"] == "incomplete" and not out["saved"]
    assert results(out, "estimate_plan")[-1]["budget_gap"] > 0


def test_tiny_deadline_overrun_is_not_rounded_into_success(store):
    out = run_agent(request(deadline=(NOW + timedelta(minutes=45, microseconds=-1)).isoformat()), MockModel(), store, NOW)
    assert out["status"] == "incomplete" and not out["saved"]


def test_reestimate_on_resume_uses_new_clock(store):
    from content_planner.agent import new_state
    state = new_state()
    tools = Tools(request(), state, store, NOW)
    tools.execute("search_assets", {"query": "毕业季", "reason": "测试检索"})
    params = {"asset_ids": ["A01"], "script_tier": "premium", "reason": "测试估算"}
    first = tools.execute("estimate_plan", params)
    tools.now = NOW + timedelta(days=2)
    second = tools.execute("estimate_plan", params)
    assert first["feasible"] and not second["feasible"]
    assert second["quote_id"] != first["quote_id"]


@pytest.mark.parametrize("url", ["http://api.deepseek.com", "https://evil.example", "https://api.deepseek.com@evil.example"])
def test_api_cannot_send_key_to_other_hosts(monkeypatch, url):
    monkeypatch.setenv("DEEPSEEK_BASE_URL", url)
    with pytest.raises(ModelError):
        DeepSeekModel()
