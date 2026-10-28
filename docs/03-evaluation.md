# 03 · 验证与限制

## 验证层次

工程测试使用Mock或针对性异常模型验证约束；离线批量运行验证CLI背后的完整链路；真实DeepSeek批量运行验证模型看到观察后的实际动作。三者不能互相替代。

所有已提交JSON均由程序实际运行产生，包含完整Trace，不是预写决策日志。真实模式使用用户配置的 deepseek-flash，非思考模式、temperature=0；9组场景中，缺信息与重复请求有意不调用模型。

## 结果

工程测试：**40 passed**，见 [pytest 实际输出](../examples/test-results.txt)；环境信息见 [environment.json](../examples/environment.json)。

批量汇总：[真实模型](../examples/deepseek/summary.json)、[离线Mock](../examples/mock/summary.json)。每组输入均见下表，输出的 `trace` 即对应运行记录。

| 场景 | 输入 | 真实输出 | 预期/实际 |
| --- | --- | --- | --- |
| 正常 | [normal](../examples/inputs/normal.json) | [normal.json](../examples/deepseek/normal.json) | completed，保存费用12的方案 |
| 超预算调整 | [replan](../examples/inputs/replan.json) | [replan.json](../examples/deepseek/replan.json) | 先12，观察后改为10并保存 |
| 缺预算与截止时间 | [missing](../examples/inputs/missing.json) | [missing.json](../examples/deepseek/missing.json) | needs_input，列明缺项，无模型调用 |
| 保存工具失败 | [tool_failure](../examples/inputs/tool_failure.json) | [tool_failure.json](../examples/deepseek/tool_failure.json) | incomplete，saved=false，保留脚本 |
| 失败后续跑 | 同上，移除故障 | [resume.json](../examples/deepseek/resume.json) | completed，复用同一脚本，无第二次生成 |
| 重复执行 | 同normal | [duplicate.json](../examples/deepseek/duplicate.json) | replayed=true，零新模型调用，不重复保存 |
| 无可行预算 | [impossible](../examples/inputs/impossible.json) | [impossible.json](../examples/deepseek/impossible.json) | incomplete，不保存，列出预算缺口 |
| 截止时间调整 | [deadline](../examples/inputs/deadline.json) | [deadline.json](../examples/deepseek/deadline.json) | 精品120分钟不满足60分钟窗口；调整后不晚于截止时间 |
| 无素材 | [no_assets](../examples/inputs/no_assets.json) | [no_assets.json](../examples/deepseek/no_assets.json) | needs_input，无关素材不用于保存 |

每次批量运行创建新数据库，故障和续跑共享请求，正常和重复共享请求。最终应仅4份已保存方案（正常、重规划、恢复、截止时间调整）。输入校验非法值、步数上限、模型故障、引用未知素材、强行宣称完成、非法工具参数、资料费用变化等边界由tests覆盖。

## 核对动态决策

真实重规划选择的是 **A01 + basic，费用10**，而不是离线模拟器的 A02 + basic、费用8。这是两种满足硬约束的选择：模型保留了更高品质素材，不应把“没有选8”判断为失败，也不把其宣称为全局最优。

比较正常/重规划Trace中 `estimate_plan` 观察后的下一动作：正常进入generate_script，超预算进入第二次estimate_plan且脚本参数改变。保存失败后下一动作变成finish(incomplete)。这满足“模型观察不同工具结果后调整动作”，不以固定重试冒充决策。

真实续跑可能重新调用估算或检索，但必须保留成功脚本，不能将失败步骤变为已完成。实际记录中成功generate_script只出现一次。重复请求的Trace保留原运行历史，`usage_this_run`为零，不意味着历史上没有调用模型。

## 失败与改进记录

首次pytest运行因本机默认临时目录访问权限报错（10通过、23个fixture设置错误），不是业务断言失败；改用仓库内的 `--basetemp .runtime/...` 后通过。后续补充了微小预算/时间越界、跨时钟报价缓存及空字段处理测试，最终40项通过。

开发环境的默认网络不能直接访问DeepSeek；在允许联网的执行环境中完成真实测试。GitHub首次连接被重置，后续仓库读取成功；具体提交结果以仓库最终commit为准。

## 已知限制与未完成项

1. 这是9组人工合成场景的单次真实模型评估，不是统计显著的决策成功率；模型升级或采样变化可能改变路径。评估脚本会检查观察驱动的重规划，未发生则报告失败。
2. 素材只覆盖毕业季和咖啡店，标签检索不具备开放域语义检索能力；文案为本地模板，不能证明创意质量。
3. 预算只约束合成方案，未设置真实模型API货币预算；通过调用上限和超时限制执行规模，但不保证特定API账单。
4. 截止时间基于模拟耗时与固定评估时钟，不包含真实生产、排期资源冲突或API等待。测试固定历史日期只为可复现。
5. 正常返回失败可续跑；进程突然终止会回滚本轮事务。没有生产级逐步检查点、跨进程并发调度和断电恢复承诺。
6. 相同request_id不能修改输入，补齐信息需新ID；尚未实现交互式多轮补全需求。
7. 未实现界面、上线部署、真实采购/发布、多Agent与框架Runtime。候选人的独立复核与学习记录须由本人实际完成后追加。
