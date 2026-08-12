# E0 发布候选报告

日期：2026-08-09  
结论：通过  
验证主机：SolidWorks 2025 SP5，revision `33.5.0`

## 发布候选范围

E0 基于 D4 后的最终默认工具面、仓库 PlannerEngine、ViewPlan 1.4 原生 C# 事务和独立
DrawingPlan 1.0 兼容 MCP 完成收口。默认 MCP 保持六项工程语义工具、零 prompts；兼容 MCP 保持独立
三工具入口，不加入默认 Codex 注册，不执行 ViewPlan/DrawingPlan 协议转换。

发布候选修正了一项长事务配置：私有 `execute_drawing_plan` 和 `verify_drawing_plan` 现在与 ViewPlan
创建/核验事务共同使用 `VIEW_PLAN_TIMEOUT`（默认 180 秒），避免真实 SolidWorks 保存、关闭和只读重开
超过普通 30 秒请求窗口。聚焦回归测试覆盖四项长事务的超时路由。

## 验证结果

| 门禁 | 结果 | 证据 |
|---|---:|---|
| 原生主机预检 | pass | 零 warning、零 blocker，保留既有用户 SolidWorks 会话 |
| E0 统一矩阵 | 6/6 | offline 3、integration 2、live 1 |
| Planner 测试 | 56 passed | `drawing_planner/tests` |
| 默认/兼容语义 MCP 测试 | 44 passed，7 subtests | `adapters/claude/tests` |
| feature compiler | 36/36 | `solidworks-compiler/pycompiler/tests/test_compiler.py` |
| C# ViewPlan 合同 | 45/45 | Roslyn x64 合同运行器 |
| ViewPlan 实机矩阵 | 13/13 | 保存、关闭、只读重开及独立核验全部通过 |
| DrawingPlan 1.0 stdio MCP | 3/3 | validate/create/verify 全部通过，状态 `0 -> 1 -> 1` |
| `validation/` 只读护栏 | unchanged | 树哈希 `0638a043ab5bcec518a6437f879b4705f33fa0ad36b25676f4e34b47aa759d7e` |

接受的本地主证据位于：

- `.host-preflight/e0-rc-20260809/host-preflight/host-preflight-report.json`
- `.host-preflight/e0-rc-20260809/full-matrix-3/view-plan-validation-matrix.json`
- `.host-preflight/e0-rc-20260809/full-matrix-3/live-artifacts/work/view-plan-live-matrix.json`
- `.host-preflight/e0-rc-20260809/drawing-plan-compat-smoke-1/drawing-plan-compat-live-smoke.json`

兼容 MCP 冒烟生成的工程图 SHA-256 为
`529dd2dbd9153b05735b8bef7fe1c2841c28eb23f7a503ac70705a83299ffad3`，核验侧车 SHA-256 为
`2c1c79ab95270966953dd9909dece5e7556ea940ecb5dcd9da52d8648ecbc6f6`。

## 复现命令

所有输出目录必须是全新的非 `validation/` 目录。

```powershell
.\.venv\Scripts\python.exe scripts\run_view_plan_validation_matrix.py `
  --lanes offline integration live `
  --host-preflight-report <host-preflight-report.json> `
  --output-dir <fresh-matrix-output>

.\.venv\Scripts\python.exe scripts\run_drawing_plan_compat_live_smoke.py `
  --repository-root . `
  --validation-dir validation `
  --output-dir <fresh-compat-smoke-output> `
  --execution-exe <matrix-output>\live-artifacts\runtime\SolidworksExecution.exe
```

冒烟运行器通过真实 stdio MCP 客户端调用独立兼容服务器，只终止本轮拥有的 MCP 和 Execution Service
子进程，并在报告中记录工具集合、响应、状态版本、运行时哈希、输出工程图/侧车哈希及验证输入前后快照。

## 已知限制

SolidWorks 2025 原生辅助视图 API 仍忽略 `show_arrow=false` 且无箭头可见性 setter；该组合继续在 COM 前
fail-closed。此限制不影响 E0 已支持能力或发布候选结论。
