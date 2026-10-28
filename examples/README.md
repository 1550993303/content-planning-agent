# 运行与验证材料

- `inputs/`：合成业务输入。缺失信息的输入有意不包含预算、截止时间。
- `mock/`：规则模拟器实际输出；不证明真实模型决策效果。
- `deepseek/`：真实 deepseek-flash 运行输出；业务工具仍使用本地合成数据。
- `test-results.txt`：pytest实际终端结果。
- `environment.json`：实际Python、pytest与SQLite版本。
- `development-session.json`：本轮自动实现与验证阶段的计时边界。

每个场景JSON同时包含最终结果和完整 `trace`。`model_action` 记录模型选中的工具、参数与公开行动依据；其后的 `tool_result` 记录执行结果。比较下一条 `model_action`，即可核对模型是否根据观察作了调整。没有记录或要求提交内部思维。

`resume.json` 包含第一次失败和第二次恢复的累计Trace，可按 `run` 字段区分。`duplicate.json` 保留原历史，`replayed=true` 和零 `usage_this_run` 表示本次没有新模型调用。不要把历史Trace误认为本次重复执行了全部步骤。

所有场景使用 `2026-10-28T10:00:00+08:00` 作为固定评估时钟，真实请求时间由 `summary.json.recorded_at_utc` 记录。固定时钟只用于复现业务截止时间，不表示API调用发生在该时刻。

复现命令（项目根目录）：

```bash
python scripts/evaluate.py --model mock
python scripts/evaluate.py --model deepseek
```

评估会覆盖对应目录中的旧记录；可用 `--out .runtime/review-run` 保留原始证据。脚本返回0表示全部断言通过，非零表示有未满足预期的场景，不能只看程序是否退出。
