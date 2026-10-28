# 内容任务规划 Agent

小影校招 AI Agent 开发岗位笔试 · **第二题：内容任务规划 Agent**。

输入主题、预算与截止时间，Agent 通过模型选择工具，观察估算或错误结果，调整方案或明确停止。最终返回任务步骤、工具产物、费用、保存状态与完整 Trace。仅保存本地合成活动方案，不购买素材、不执行活动、不接发布。

## 评审入口

- [需求与成功标准](docs/01-requirements.md)
- [设计、动态决策和取舍](docs/02-solution.md)
- [测试结果、验收映射和限制](docs/03-evaluation.md)
- [AI 使用与贡献边界](docs/04-ai-usage.md)
- [复盘与实际用时](docs/05-retrospective.md)
- [真实 DeepSeek 验收汇总](examples/deepseek/summary.json) 与 [超预算后的实际 Trace](examples/deepseek/replan.json)
- [离线验收汇总](examples/mock/summary.json)

**真实模型与 Mock 的边界：** `--model deepseek` 使用真实的 `deepseek-flash` 工具调用；`--model mock` 是规则模拟器，只验证工程流程。两种模式的素材检索、脚本模板和费用表都是本地合成业务工具。真实模式证明的是模型根据工具结果作决策，不代表真实市场报价、脚本创意质量或生产效果。

已提交的真实运行中，预算 10 的请求先估算为 12，模型随后将脚本档位从 premium 改为 basic，重新估算为 10 并保存。离线模拟器选取了另一条可行路径（更换素材与脚本，12 → 8）；两者无需选择相同方案。

## 环境与安装

Python **3.11+**；运行仅用标准库，无 Agent 框架、无数据库服务。SQLite 随 Python 提供。测试环境为 Python 3.13.9。

```bash
git clone https://github.com/1550993303/content-planning-agent.git
cd content-planning-agent
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m pip install -r requirements.txt
```

macOS / Linux：

```bash
source .venv/bin/activate
python -m pip install -e .
python -m pip install -r requirements.txt
```

也可不安装项目包，在 PowerShell 中设置 `$env:PYTHONPATH="src"`，或在 bash 中设置 `export PYTHONPATH=src`，然后使用下面的 `python -m content_planner` 命令。`scripts/evaluate.py` 自带源码路径，可直接运行。

## 离线运行：无需密钥

```bash
python -m content_planner examples/inputs/replan.json --model mock --now 2026-10-28T10:00:00+08:00 --output .runtime/demo.json
```

默认数据库为 `.runtime/planner.sqlite3`。完整输出和 Trace 写入 `--output` 指定的 JSON，并打印到终端。此例结果为 `completed`、`saved=true`、`total_cost=8`。

示例的 `--now` 是**固定评估时钟**，用于复现历史截止时间；真实业务输入请提供未来截止时间并省略 `--now`，使用实际当前时间。所有输入时间必须显式包含时区。耗时为合成串行任务的预计分钟数，不是等待 API 的真实运行时间。

## 真实 DeepSeek 模型

程序直接读取系统变量 `DEEPSEEK_API_KEY`。如果已经配置，重开终端使环境变量生效即可；不要将密钥写进代码、输入 JSON 或命令参数。`.env.example` 仅说明变量，程序**不会自动加载 `.env`**。

可选配置（PowerShell）：

```powershell
$env:DEEPSEEK_BASE_URL="https://api.deepseek.com"
$env:DEEPSEEK_MODEL="deepseek-flash"
python -m content_planner examples/inputs/replan.json --model deepseek --db .runtime/real-demo.sqlite3 --now 2026-10-28T10:00:00+08:00 --output .runtime/real-demo.json
```

默认使用上述地址和模型；为防止将密钥误发到其他主机，地址仅允许 DeepSeek 官方 HTTPS 主机。模型使用非思考模式的原生 `tools` / `tool_calls` / `tool` 消息，不记录内部思维；API 失败不会暗中降级成 Mock。API 调用会消耗服务商额度，业务预算中的模拟单位不包含 API 账单。接入格式参考 [DeepSeek 首次调用](https://api-docs.deepseek.com/) 和 [Tool Calls 文档](https://api-docs.deepseek.com/guides/tool_calls/)。

## 批量验证与测试

```bash
python -m pytest -q
python scripts/evaluate.py --model mock
python scripts/evaluate.py --model deepseek
```

批量评估会创建全新的本地数据库，按顺序运行 9 组场景：正常、超预算重规划、缺信息、保存失败、失败续跑、重复请求、无可行预算、截止时间调整、无素材。输出写入 `examples/mock/` 或 `examples/deepseek/`，会替换该目录的既有评估证据；希望保留旧记录时，使用 `--out .runtime/new-evaluation`。脚本检查状态、重规划、续跑复用、重复请求零调用及最终方案数量，任一不通过则非零退出。真实模型可能选择不同的可行方案，不能保证逐字或逐步相同。

若测试环境的默认临时目录不可写，可使用：

```bash
python -m pytest -q --basetemp .runtime/pytest-local
```

## 故障注入、续跑与幂等

```bash
python -m content_planner examples/inputs/tool_failure.json --model mock --db .runtime/resume.sqlite3 --now 2026-10-28T10:00:00+08:00 --fault save_plan:always --output .runtime/failed.json
python -m content_planner examples/inputs/tool_failure.json --model mock --db .runtime/resume.sqlite3 --now 2026-10-28T10:00:00+08:00 --output .runtime/resumed.json
python -m content_planner examples/inputs/tool_failure.json --model mock --db .runtime/resume.sqlite3 --now 2026-10-28T10:00:00+08:00 --output .runtime/duplicate.json
```

第一条有意返回未完成（退出码 2），已有脚本保留；第二条去掉故障后使用同一请求和数据库续跑；第三条回放完成结果，`replayed=true`，不再调用模型或重复保存。

`--fault generate_script:1` 表示本轮第一次脚本工具调用失败；模型观察失败结果后可以自行重试。故障配置不写入业务请求，续跑时可以移除。每次运行重新计数，缓存成功结果优先返回，不重复注入已完成步骤。

请求 ID 与输入指纹绑定，同一 ID 修改预算等字段会拒绝执行；更改需求或补齐缺失信息时使用**新的 request_id**。单次默认最多 12 轮模型选择、12 次工具调用（包括 finish 和失败调用），分别可用 `--max-steps`、`--max-tool-calls` 调整，范围 1–40。达到上限返回明确的 `incomplete`，不会宣称完成。

## 输入与输出约定

输入示例见 [examples/inputs](examples/inputs)。预算为非负有限数字；主题非空；截止时间为含时区的 ISO 8601 字符串。`preferences` 是软偏好，预算和截止时间是硬约束。

输出字段：`status`、`message`、`missing_fields`、`steps`、`partial_results`、`saved_plan`、`saved`、`total_cost`、`model`、`trace`。未保存时 `total_cost=null`，候选费用和缺口在 `partial_results.quotes` 中，避免将候选方案当作最终方案。`saved=true` 表示 SQLite 中已有本地方案；整项任务是否完成以 `status` 为准。实际工具结果都保留在 Trace 中。

退出码：`0` 完成；`2` 待补充或未完成；`1` 输入非法、身份冲突、存储错误等运行错误。真实模型不可用时任务返回 `incomplete`。若输出文件写入失败，数据库中可能已保存方案，返回运行错误；再次使用同一请求可回放已保存状态。

## 目录

```text
src/content_planner/   模型适配、Agent 循环、工具、存储、CLI
src/content_planner/data/catalog.json  合成素材与费用/耗时单一数据源
data/                 合成资料及业务规则说明
tests/                可执行工程测试
examples/inputs/       评估输入
examples/mock/         离线实际输出及 Trace
examples/deepseek/     真实模型实际输出及 Trace
docs/                 五份交付文档
scripts/evaluate.py   可复现批量评估
.env.example          环境变量说明，不含密钥
```

已知限制、未完成项与诚实的 AI 贡献记录均见 `docs/`。本项目未实现线上部署、真实素材服务、多 Agent 或生产级 Runtime。
