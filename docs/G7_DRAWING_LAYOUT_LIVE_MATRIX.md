# G7 最终布局真实矩阵

状态：完成（2026-08-15）；真实矩阵、独立核验和生产能力晋级均已通过

目标环境：SolidWorks 2025 SP5，revision `33.5.0`

## 1. 固定范围

G7 请求固定九个正向场景：少尺寸、多视图、剖视、详图、辅助视图、孔阵列、高密度尺寸、缩放、
已授权换图幅；另固定一个未授权换图幅反例。场景名称不能只是人工标签：聚合前会从不可变
DrawingLayoutPlan、源 DimensionPlan 和源 ViewPlan 证明对应的尺寸数量、视图类型或操作类型。九个正向
场景必须使用不同的已发布计划。

每个正向案例执行同一条链：

```text
validate (production capability_blocked)
  -> qualify (G7 only)
  -> save / close / read-only reopen
  -> independent qualification verify
  -> immutable case evidence
```

资格入口只允许 `planned` 或 `supported` 的 G4/G5 操作和安全能力参与实证；任何 `unsupported` 操作、
安全能力或 G0 边界都会在连接 SolidWorks 前拒绝。资格入口不改写生产能力目录，也不属于第五 Skill
的 allow-list。矩阵完成后由聚合器单独生成晋级候选，再经字节哈希比对后人工纳入仓库生产目录。

反例重新调用确定性发布入口，并且只有返回 `sheet-format-unauthorized`、不发布计划时才能形成证据。

## 2. 证据与晋级

G7 增加三项严格 Schema：矩阵请求、单案例证据和汇总。正向证据绑定执行服务、生产能力目录、完整
LayoutPlanningRequest、嵌套 DimensionPlanningRequest、计划文件/规范哈希、最终图纸、G4 侧车和三个
语义阶段。聚合时重新校验当前磁盘文件、侧车的递归冻结输入、尺寸/视图/对象身份、无悬空引线、
安全区、碰撞、保存重开和独立布局指纹。

只有十个场景齐全、八类操作均被真实案例覆盖、六项安全读回均通过，才能输出独立的
`plan-current.json` 晋级候选。运行器从不替换生产 `current.json` 或 `plan-current.json`。

## 3. 完成证据

最终不可变矩阵位于
`C:\Users\admin\Downloads\solidworks-mcp-test-g7-20260815\layout-g7-matrix-r9`。G0 使用
`G0-EXACT-COMPLETE-20260815` 资格制品，11 项边界全部为 `supported`。G7 汇总 SHA-256 为
`91e95b5c34ad92ac422839d6eb5585983336117bae7dbfc113f8e68be1122ecc`，状态为 `complete`：

- 九个正向场景全部完成 production validate、资格创建、保存/关闭/只读重开及独立资格核验；
- 一个未授权换图幅反例稳定返回 `sheet-format-unauthorized`，且不发布计划；
- 八类操作覆盖计数均大于零，六类安全读回在九个正向场景中各通过九次；
- 所有源文件哈希保持不变，矩阵执行服务 SHA-256 为
  `dfc485234501ee9c38137de4f319d08cc0edb013e1a4588dc237b42fc54e197d`；
- 晋级候选 SHA-256 为
  `01c6dfd5f25e75ad347dc40ccf16a292a646b6811863dc68678f228ed64b4fe9`，与仓库
  `drawing_layout_planner/capabilities/plan-current.json` 字节一致。

晋级后又通过正式生产入口冒烟：`validate_part_drawing_layout_plan` 返回 `supported`，
`create_final_part_drawing` 与 `verify_final_part_drawing` 均返回 `COMPLETED`。最终工程图 SHA-256 为
`aa3a02cfb0c1b34f27c1eb35a0dab8f16029a95bee1bf33d2422f4f36b796996`，验证侧车 SHA-256 为
`eb8177b4da4396b6cdf225ed6685b3ecc8bb2f690ca283fd6786a2e2668ecafc`。

孔阵列案例额外证明 17 个尺寸、孔数量/孔间距语义、注释和单引线在保存重开后不漂移。SolidWorks 会
在保存时对注释侧引线节点和文字包围框做亚毫米级原生归一化；事务仍严格核验引线附着端和真实碰撞，
只在这些检查通过后把已授权注释/引线路径映射到计划规范指纹，再要求保存后、事务只读重开和独立核验
三者指纹相同。

## 4. 运行方式

准备并一次性发布符合合同的矩阵请求后，使用已运行的仓库 Execution Service：

```powershell
.\.venv\Scripts\python.exe .\scripts\run_drawing_layout_g7_live_matrix.py `
  --request C:\path\to\drawing-layout-g7-matrix-request.json `
  --summary-output C:\path\to\drawing-layout-g7-summary.json `
  --execution-service-path D:\solidworks-mcp\solidworks-execution\SolidworksExecution\bin\Debug\SolidworksExecution.exe `
  --execution-pid 12345 `
  --promotion-candidate-output C:\path\to\drawing-layout-plan-capabilities.candidate.json
```

运行器锁定进程镜像和默认 24 项语义工具，保护矩阵请求、G0 资格、生产能力目录、执行服务及全部递归
上游制品哈希；输出和侧车必须是新路径。矩阵只能在晋级前对 `capability_blocked` 的生产目录运行；
完成证据一旦生成不可覆盖，后续复跑必须使用全新输出根目录。
