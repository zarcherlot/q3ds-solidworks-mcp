# 原 MCP 内置工程图视图规划开发计划

状态：E0 发布候选已完成  
最后更新：2026-08-11  
目标协议：`solidworks-view-plan` schema 1.4

## 1. 目标

将主视图选择、最小视图集、剖视/断面/局部/辅助视图决策、特征覆盖和图纸布局规划
建设为本仓库的正式业务能力。`solidworks-plan-drawing-views` 仅作为工程规则和契约设计
的参考来源，不作为生产运行时依赖。

生产调用链必须保持：

```text
Codex / MCP client
  -> repository semantic MCP
  -> repository PlannerEngine or explicit upper-layer planning Skill
  -> repository execution_client
  -> repository C# Execution Service
  -> private atomic SolidWorks COM operations
  -> save / close / read-only reopen / verify
```

## 2. 不可违反的边界

- Agent 只能调用工程语义级工具；COM 方法、选择动作、视图创建步骤、保存步骤不得暴露。
- Python 规划层不调用 SolidWorks COM；C# Execution Service 是唯一 COM 边界。
- 模型输出始终是不可信候选，必须依次通过输入完整性、JSON Schema、工程语义、特征
  覆盖、布局和执行能力校验。
- 工程正确性与当前执行能力分离。执行器缺少能力时返回 `capability_blocked`，不得删除
  视图、替换视图类型、使用隐藏线代替剖视或修改冻结几何。
- 创建类操作必须使用新输出路径，执行事务必须保存、关闭、只读重开并核验持久化结果。
- `validation/` 是验证数据，只读使用，不得由开发测试覆盖或清理。

## 3. 目标语义工具面

| 工具 | 责任 | SolidWorks 写入 |
|---|---|---:|
| `solidworks_status` | 报告 MCP、Planner、执行器和 SolidWorks 状态 | 可选启动 |
| `inspect_part_for_drawing` | 只读检查零件、配置、本地化标准视图名和包围盒 | 否 |
| `initialize_part_drawing_handoff` | C# 事务生成空白图纸、两份报告、六视图图片及 manifest-last 冻结交接 | 是 |
| `plan_part_drawing_views` | 调用 PlannerEngine，发布通过确定性校验的冻结计划 | 否 |
| `publish_validated_part_drawing_view_plan` | 重校验并原子发布显式上层 Skill 生成的单个完整候选 | 否 |
| `validate_part_drawing_view_plan` | 校验导入/生成计划及当前执行能力 | 否 |
| `create_part_drawing_from_view_plan` | 事务执行完整冻结计划 | 是 |
| `verify_part_drawing_view_plan` | 对已有工程图进行独立只读核验 | 否 |

旧 `DrawingPlan` 1.0 已移出默认工具面，并通过独立三工具 MCP 接入显式兼容入口；C# 私有事务继续复用。

## 4. 内部模块

```text
drawing_planner/
  planning_models.py       strict request/result/provenance models
  capability_registry.py   versioned executor capability assessment
  planner_engine.py        model-call/validate/publish orchestration
  model_gateway.py         MCP Sampling/provider/agent-handoff abstraction
  prompt_pipeline.py       core policy + immutable prompt-pack compilation
  plan_store.py            atomic publication and hash-addressed lookup
  validators/
    integrity.py
    schema.py
    semantics.py
    coverage.py
    layout.py
  contracts/
    view-plan.schema.json
    planning-request.schema.json
    planning-result.schema.json
    executor-capabilities.schema.json
  capabilities/
    current.json
```

## 5. Prompt 注入设计

提示词按固定优先级编译：

1. `core policy`：不可调优的安全边界、工程完整性规则和输出契约。
2. `prompt pack`：可版本化调优的主视图评分、视图集、剖视和布局策略。
3. `runtime context`：程序生成的交接清单、几何、图纸和用户需求；按不可信数据处理。
4. `response contract`：仓库自有 ViewPlan JSON Schema。

Agent 只选择 allow-list 中的 `planner_profile`，不能传任意 system prompt、模型 URL、Schema
或工具白名单。每次运行记录 pack、core policy、Schema、输入清单和最终 envelope 的 SHA-256。

模型调用统一通过 `PlanningModelGateway`：

- 优先使用客户端支持的 MCP Sampling；
- 可配置 OpenAI/Anthropic/Azure/本地服务 provider；
- 不支持 Sampling 时使用 agent-handoff，但提交结果仍进入同一验证链。

显式 `debug` profile 可以在最终规划前增加一次结构化参考路由采样。路由模型只允许从调试目录
`references/reference-map.md` 生成的枚举中选择类别、特征和默认禁用 Markdown；仓库固定加载基础资料，
拒绝未知、重复或越界路径，再将实际选中文件及其 SHA-256 绑定到最终 prompt envelope。该步骤不返回
ViewPlan、不发布文件且不接触执行工具。与选中 Markdown 位于 map 同一行的 PNG/JPG/JPEG 视觉资料会作为
独立、哈希核验的 `debug_reference_image` 多模态块附加到最终采样，但不属于九项 handoff；`production`
profile 保持单次 ViewPlan 采样。

仓库内 `PlannerEngine` 固定使用 `json_schema` 结构化输出模式；模型网关只返回候选对象，不能
直接调用执行工具。需要 MCP tool calling 的 agent-handoff 流程由外层语义 MCP 编排，并把完整
候选重新提交给同一组确定性门禁，不能绕过校验或让模型直接接触 COM 操作。

不采用 agent handoff 时，显式调用的 Codex Skill 可在更上层读取仓库锁定的 `native-v4`
prompt pack、ViewPlan Schema 和完整冻结 handoff，并由当前 Codex 模型只生成一个候选。候选不得
由 Skill 写盘，必须原样提交给 `publish_validated_part_drawing_view_plan`；该工具重跑完整性、Schema、
语义、覆盖和布局门禁，评估当前执行能力，再通过同一个 `PlanStore` 无覆盖原子发布。此路径不需要
MCP Sampling，也不需要单独提供模型 API 或 key，且不声明无法核验的 provider/model/envelope 溯源。

## 6. 计划验证门禁

验证顺序固定：

1. `integrity`：路径、协议、配置、显示状态和全部 SHA-256；
2. `schema`：Draft 2020-12、未知字段和各视图判别联合；
3. `semantics`：ID、引用、父子 DAG、来源、方向和剖切数学关系；
4. `coverage`：每个强制表达要求恰好由合适视图覆盖；
5. `layout`：安全区、保留区、视图重叠和 25/35 mm 尺寸带；
6. `capability`：当前 C# 执行器能否逐项精确实现和回读。

前五项通过即可形成工程上有效的冻结计划；第六项不通过时计划仍保留，但状态必须是
`capability_blocked`，创建工具拒绝执行。

`schema` 使用 Draft 2020-12 并启用 RFC 3339 `date-time` 格式检查。`semantics` 还会校验
profile 对应的受信 producer/ruleset、证据 JSON Pointer、第一/第三角投影位置、显式方向正交性、
剖切路径及特征轴关系；`coverage` 禁止以隐藏线或不兼容视图替代剖视；`layout` 对所有矩形使用
冻结图纸坐标，矩形边界相接允许、正面积重叠拒绝。完整性或 Schema 前置条件失败时，后续门禁
标记为 `not_run`，不会在不可靠结构上继续推断。

## 7. C# 执行器演进

C# 私有协议使用 `initialize_part_drawing_handoff`、`validate_frozen_part_drawing_view_plan`、`execute_part_drawing_view_plan` 和
`verify_committed_part_drawing_view_plan`，直接接收 ViewPlan 1.4；不通过 Agent 可见的低级工具
拼装。内部能力按以下顺序补齐：

1. `model_view`；
2. `projected_view`；
3. `full_section`；
4. `half_section`、`offset_section`、`aligned_section`、`removed_section`；
5. `broken_out_section`；
6. `detail_view`；
7. `auxiliary_view`；
8. 中心标记和圆周组；
9. 对称中心线；
10. 标签、唯一选择、布局及全部持久化回读。

每项能力必须同时具备：输入校验、唯一选择、原生创建、失败代码、保存重开回读、自动化
测试和一次真实 SolidWorks 验证，之后才能在能力清单中标记为 `supported`。

## 8. 分阶段执行清单

### A. 原 MCP 规划基础

- [x] A0：确认原 MCP Python/C# 主链和临时外部桥接边界。
- [x] A1：加入严格规划领域模型和版本化执行能力注册表。
- [x] A2：将 ViewPlan 1.4 Schema 纳入仓库并记录来源与 SHA-256。
- [x] A3：实现输入完整性与 Schema 校验器。
- [x] A4：实现 PlannerEngine、ModelGateway 协议和审计结果模型。
- [x] A5：实现语义、覆盖和布局校验器。
- [x] A6：实现原子 PlanStore，并新增 `plan_part_drawing_views`。

验收：规划工具不启动 SolidWorks；相同输入/profile 得到可追踪 envelope；模型候选只有通过
前五层门禁才可发布；测试覆盖注入攻击、未知字段、哈希漂移、引用环、覆盖缺失和布局冲突。

### B. ViewPlan 1.4 基础执行

- [x] B1：C# 接收并严格解析 ViewPlan 1.4。
- [x] B2：实现 `model_view` 和 `projected_view`。
- [x] B3：实现事务保存、关闭、只读重开和逐视图回读。
- [x] B4：注册 `validate/create/verify_part_drawing_view_plan` 语义工具。

验收：支持子集完全通过持久化核验；任何其他视图稳定返回能力错误，不发生降级或部分提交。

### C. 剖视、局部与辅助视图

- [x] C1：完整剖视族。
- [x] C2：局部剖和局部视图。
- [x] C3：辅助视图。
- [x] C4：中心标记、中心线和 detail/auxiliary 标签。
  - [x] 特征绑定中心标记和对称中心线。
  - [x] detail 显式标签位置。
  - [x] auxiliary 显式标签位置（父视图所有的仓库受管原生 note，严格回读）。

### D. 默认链切换

- [x] D1：使用 `validation/` 完成离线、集成和真实 SolidWorks 验证矩阵。
  - [x] 仓库统一矩阵运行器、分层 JSON 报告和稳定退出码。
  - [x] `validation/` 全树执行前后 SHA-256 不可变护栏。
  - [x] Planner/编译器离线门禁与语义 MCP/C# 私有契约集成门禁。
  - [x] 仓库自有的真实 SolidWorks 全能力重跑入口及新生成制品矩阵。
- [x] D2：ViewPlan 1.4 成为默认工程图协议。
- [x] D3：移除外部 Skill/CLI 运行时依赖。
- [x] D4：旧 DrawingPlan 1.0 移至显式兼容入口。

### E. 发布候选

- [x] E0：发布候选收口。
  - [x] 基于 D4 最终代码重跑 offline/integration/live 六案例统一矩阵。
  - [x] SolidWorks 2025 SP5 全能力 live 矩阵 13/13 通过。
  - [x] 通过独立 stdio MCP 完成 DrawingPlan 1.0 validate/create/verify 实机事务。
  - [x] `validation/` 四项输入在全部发布候选验证前后 SHA-256 完全不变。
  - [x] 发布候选报告、README 和 CHANGELOG 与最终证据同步。

## 9. 每次提交的完成定义

- 默认 MCP 工具契约测试通过；
- Planner 单元和 JSON Schema 测试通过；
- C# 合同测试 `45/45` 不回归；
- Python `compileall` 和 `git diff --check` 通过；
- C# 改动完成 x64 构建；
- COM 行为改动必须记录 SolidWorks 版本及真实验证结果；
- 文档中的阶段勾选和能力清单与代码同步。

## 10. 当前迁移说明

上一版 `drawing_planner/executor_bridge.py` 及其两个外部执行器动词已在 D3 删除。生产链不再发现
Codex Skill 目录、调用 `Invoke-ViewPlanCli.ps1` 或通过 PowerShell 子进程执行冻结计划。

A2/A3 已完成：PromptCompiler 只读取仓库内锁定的 ViewPlan 1.4 Schema；完整性校验会重算
handoff manifest、模型、空白工程图、两份报告和六张图片的 SHA-256，并核对计划中的路径、
配置、显示状态及图纸上下文；完整性或 Schema 前置条件失败时，后续门禁不会运行。

A4 已完成：`PlannerEngine` 在编译 prompt 和调用模型前执行输入完整性门禁；生产
`RepositoryPlanningPromptCompiler` 只接受 allow-list profile，并把完整 handoff、core policy、
prompt pack、ViewPlan Schema 和 envelope 哈希绑定到调用；`CallablePlanningModelGateway` 固定
provider/model 身份、超时和严格结构化返回契约。结果审计记录 planning request、模型候选和
能力清单版本，输入失败时后续门禁标记为 `not_run` 且不会调用模型。

A5 已完成：`RepositoryViewPlanValidator` 已接通语义、覆盖和布局三层门禁，包含 ID/引用/DAG、
受信规则集、证据指针、方向与投影关系、剖切数学、特征到兼容视图的一次性覆盖、安全区、
保留区、标签、计划框及 25/35 mm 尺寸带检查。有效候选现在可以进入能力评估并原子发布；
执行器能力仍全部按 `current.json` 如实评估，缺失能力只产生 `capability_blocked`，不发生降级。

A6 已完成：默认 MCP 新增工程语义级 `plan_part_drawing_views`。它只接收严格
`PlanningRequest`，通过 MCP Sampling 将精确 ViewPlan 1.4 Schema 作为唯一提交工具，并注入经
SHA-256 核验的 handoff、readiness、geometry 与六张标准视图；采样前再次核对全部证据哈希。
模型只能返回一个候选对象，不能调用执行工具。候选通过五层门禁后按 `current.json` 评估能力，
再由 `PlanStore` 在交接目录内无覆盖原子发布；`capability_blocked` 计划保留且不发生降级。

B1 已完成：C# Execution Service 新增私有、COM-free 的 `validate_frozen_part_drawing_view_plan`
入口，直接接收结构化 ViewPlan 1.4 对象。执行器构建时从仓库唯一
`drawing_planner/contracts/view-plan.schema.json` 链接复制 Schema，并在运行时核对固定
SHA-256；独立 Draft 2020-12 子集验证器覆盖该 Schema 使用的 `$ref`、联合/条件、严格对象、
定长数组、范围、枚举、正则和 RFC 3339 格式。解析成功只返回合同摘要及
`execution_readiness: not_assessed`，不连接 SolidWorks、不改变 `state_version`，也不把计划转换为
DrawingPlan 1.0。能力清单版本升至 0.2.0，但所有视图执行能力仍如实保持 `planned`。

B2 已完成原生基础视图执行：新增 COM-free `ViewPlanBasicExecutionCompiler`，在任何 COM
操作前完成能力门禁、父子 DAG 拓扑排序、投影方向/位置/比例关系、显示样式和数值检查；
剖视与中心元素会返回稳定能力错误，不进行近似或部分执行。私有
`ViewPlanBasicViewExecutor` 直接调用 SolidWorks 原生 API 创建标准/精确命名/显式基向量模型视图和
投影视图，使用本地化标准方向、唯一父视图选择、确定性名称、比例/配置/显示状态设置及
即时回读。SolidWorks 2025 SP5 已用 `validation/` 的零件和 A3 模板完成三组不落盘验证；
显式方向使用执行器独占的只读源模型、确定性临时命名视图和严格恢复事务；实测临时名清零、
源方向恢复且模型不变脏。能力清单升至 0.4.0，但在 B3 保存关闭只读重开验证完成前仍保持 `planned`。

B3 已完成仓库原生磁盘事务：执行前重新核对模型、空白工程图、几何/就绪报告和六张标准视图图片共
十项冻结制品的绝对路径与 SHA-256；只允许全新的 `.SLDDRW` 输出及验证侧车，任何失败均清理本事务
创建的临时或部分输出。事务创建视图后保存、关闭、只读重开，并逐视图核对唯一句柄、确定性名称、
父子关系、位置、比例、方向/roll、配置、显示状态、显示/切边模式、锁定状态、源模型路径及图纸契约。
SolidWorks 2025 SP5 的十二组实机矩阵覆盖九种标准方向、精确命名+roll、显式基向量+roll、着色带边线，
且每组均包含投影视图并通过事务内和独立只读重开；`validation/` 前后哈希一致，源模型不变脏且临时
命名视图为零。能力清单升至 0.5.0，`model_view` 与 `projected_view` 标记为 `supported/live`。

B4 已完成默认 MCP 的仓库原生 ViewPlan 执行面：新增工程语义级
`validate_part_drawing_view_plan`、`create_part_drawing_from_view_plan` 和
`verify_part_drawing_view_plan`，均接收完整 ViewPlan 1.4 与原始 `PlanningRequest`。Python 边界在
每次调用时重跑完整性、Schema、语义、覆盖、布局和能力门禁；C# 私有入口再次核验 Schema、可执行
子集及十项冻结制品哈希。创建操作使用无覆盖事务并在成功后递增状态；独立验证只读打开既有图纸，
核对审计侧车与图纸哈希且不递增状态。默认 MCP 已移除外部 Skill/CLI 执行工具注册，D3 随后删除兼容
文件和外部 prompt。SolidWorks 2025 SP5 实机服务测试通过 COM-free 验证、状态 0→1 提交、重复 operation 幂等、
独立验证状态不变、缺失输出稳定失败，以及两个基础视图的保存/关闭/只读重开精确回读；测试未修改
`validation/`。

C1 已完成 `full_section`、`half_section`、`offset_section`、`aligned_section` 和
`removed_section` 的原生执行。C# 在 COM 前独立编译剖视契约、拓扑排序父子 DAG，并从已绑定且已核验
哈希的几何报告中唯一解析特征；全剖冻结特征轴，偏移剖强制每个特征轴与有限剖切段相交，点数、零长段、
半剖垂直性、旋转剖非共线性和全剖投影布局均 fail-closed。COM 原子执行只在私有 C# 执行器中选择剖切线
并调用 `CreateSectionViewAt5`。回读覆盖唯一父视图、持久化句柄、精确标签、段数、规范化 line-info、
partial/aligned/reversed、视图 alignment、比例和深度语义；保存前指纹必须与关闭后只读重开完全一致。
SolidWorks 2025 SP5 的五类实机矩阵全部通过事务内重开及独立验证，能力清单升至 0.6.0，五类剖视与
`view_labels` 标记为 `supported/live`。在 C1 交付点，`broken_out_section` 和 `detail_view` 继续由 C2
阻断，未发生降级。

C2 已完成 `broken_out_section` 和 `detail_view` 的原生执行。局部剖按独立模型方向视图编译，圆形边界
必须完整位于计划框内，COM 内通过 `CreateBreakOutSection` 创建并精确回读边界中心、半径、模型轴、
局部剖特征类型/数量和深度；原生 API 无法表达的反向局部剖在 COM 前稳定拒绝。局部视图按唯一父视图
派生，五种 style、三种 show-type、标签、轮廓开关、比例和圆 profile 全部进入冻结契约，并通过
`CreateDetailViewAt4` 创建。SolidWorks 原生管理剖视/详图名称，因此事务记录并跨保存重开精确比较
持久唯一句柄，不伪造确定性重命名。普通轮廓的 shape intensity 明确标记为 `not_applicable`；锯齿轮廓
则精确回读声明值。SolidWorks 2025 SP5 的两类实机矩阵及锯齿强度补充案例均通过保存、关闭、只读重开
和独立验证，规范化指纹稳定且未修改 `validation/`。能力清单升至 0.7.0，两类 C2 视图标记为
`supported/live`，未增加任何 Agent 可见的 COM 原子工具。

C3 已完成 `auxiliary_view` 的仓库原生执行。C# 在 COM 前冻结模型空间参考边端点、图纸匹配公差、
父视图、对齐模式、箭头、标签和翻转要求；执行时只从父视图可见原生直边中接受唯一匹配，并通过
`CreateAuxiliaryViewAt2` 创建。回读核对唯一父视图、持久句柄、类型、比例、对齐、投影箭头链接、
标签、参考边投影和完整模型到视图旋转矩阵。SolidWorks 2025 的 `IView.FlipView` 不反映辅助视图的
Flip 参数，因此执行器以父视方向、参考边方向和辅助视方向的有向关系确定真实翻转状态。对齐/未翻转
和非对齐/翻转两组 SolidWorks 2025 SP5 实机案例均通过保存、关闭、事务内只读重开和独立验证，且
`validation/` 未修改。能力清单升至 0.8.0，`auxiliary_view` 标记为 `supported/live`。实测原生接口
忽略 `show_arrow=false` 且没有箭头可见性 setter；在 C3 交付点，隐藏箭头与显式 auxiliary 标签位置由
Python 能力评估和 C# 编译器在 COM 前标记为 `capability_blocked`，不进行静默降级。

C4 已完成中心元素及 detail/auxiliary 显式标签。C# 编译器把中心标记与中心线编译为纯数据契约，并在 COM 前从
哈希已核验的 `model-geometry.json` 唯一解析 feature 对应的圆形 B-Rep 边。执行器关闭文档自动中心元素，
原生创建 `single`/`linear_group`/`circular_group` 中心标记，严格核对 style、group/count、默认值、线、
propagate、slot 和颜色；对称中心线从可见直边中筛选满足轴向、最小跨度、重叠和对称约束的唯一最外侧
边对，并精确回读 attached entities。未计划元素、SolidWorks 风格降级和最外侧并列均 fail-closed。
SolidWorks 2025 SP5 的双孔 linear group 与水平/垂直中心线案例通过保存、关闭、事务内只读重开和独立
验证，规范化指纹完全一致。detail 显式标签位置按原生 profile 周边约束执行有限角度反解，严格 getter
坐标案例同样通过三阶段回读。auxiliary 的原生 `IProjectionArrow` 不提供可用的标签位置 setter，因此
显式模式保留原生箭头、清空其不可定位文字，并在父视图内创建确定性命名、无引线且无附着实体的原生
`INote`；该 note 复制原生箭头文字格式，并以 `IAnnotation.SetPosition2` 写入冻结坐标。执行器严格回读
父视图 owner、名称、文字、可见性、格式和位置，并将受管标签与原生箭头线/标签锚点一起纳入 auxiliary
规范化指纹。SolidWorks 2025 SP5 的对齐/未翻转及非对齐/翻转两组显式案例均通过内存、事务只读重开和
独立验证，指纹完全一致。能力清单升至 1.0.0，C4 全部项目标记为 `supported/live`。`show_arrow=false`
仍因 SolidWorks 忽略隐藏请求且无可见性 setter 而在 COM 前 fail-closed；这是独立的箭头可见性限制，
不影响 C4 标签能力完成。

D1 已完成。`drawing_planner.validation_matrix` 和 `scripts/run_view_plan_validation_matrix.py` 以硬编码、
不可由运行时数据覆写的 case 清单执行离线、集成与 live
门禁，每个 case 记录 argv、超时、返回码、持续时间以及独立 stdout/stderr 文件和 SHA-256；任何离线或
集成失败都会阻止后续 live lane。运行器在执行前后递归哈希 `validation/` 全部文件，输出不得位于该目录
或覆盖非空目录，即使所有命令返回零，只要验证输入发生变化，矩阵仍整体失败。C# 合同测试新增
`scripts/run_view_plan_contract_tests.ps1`，直接使用仓库恢复的 Roslyn x64 编译器，在本机与 CI 间保持
同一组 45 项私有 ViewPlan 契约。`scripts/run_view_plan_live_matrix.ps1` 在已通过且无警告的原生主机预检后，
以 Roslyn x64 构建仓库 C# Execution Service；`scripts/run_view_plan_live_matrix.py` 只从 `validation/` 读取
唯一零件、空白工程图和 A3 模板，在全新目录生成冻结交接制品，并仅通过私有 HTTP 事务编排 C# COM 层。
SolidWorks 2025 SP5（revision `33.5.0`）的 13 案例矩阵覆盖基础投影、五类剖视、局部剖、三类详图、两类
辅助视图与中心元素，13/13 均通过校验、执行后内存回读、保存/关闭/只读重开及独立验证的规范化指纹比较；
共生成 13 张新工程图及 13 份审计侧车。统一六案例矩阵全部通过，live 临时服务正常退出，stderr 为空，
`validation/` 四项制品树前后 SHA-256 均为
`0638a043ab5bcec518a6437f879b4705f33fa0ad36b25676f4e34b47aa759d7e`。隐藏 auxiliary 箭头仍因
SolidWorks 忽略 `show_arrow=false` 而在 COM 前 fail-closed；该已知限制不改变 D1 已支持能力的重跑结论。

D2 已完成默认协议切换。`adapters/claude/server.py` 的 FastMCP 版本升至 `2.0.0`，模型指令明确
ViewPlan 1.4 是默认工具面唯一的工程图协议；默认注册从九项收敛为六项，只保留状态、只读零件预检、
仓库 PlannerEngine 规划以及 ViewPlan 校验、事务创建和独立核验。三项 DrawingPlan 1.0 helper 不再注册为
Agent 工具；C# 私有 `execute_drawing_plan`/`verify_drawing_plan` 事务及 Schema 均保留，随后由 D4 的独立
兼容 MCP 接入。`semantic-tools.schema.json` 已移除全部 DrawingPlan 1.0 引用，Codex allow-list
与 FastMCP 实际六项工具精确一致；契约测试同时锁定默认工具集合、ViewPlan 1.4 完整结构化 Schema、
无 1.0 引用和旧 helper 未注册状态。D3 随后独立清理外部运行时依赖。

D3 已完成外部 Skill/CLI 运行时清理。临时 `executor_bridge.py`、桥接测试和包导出已删除；默认 MCP 同时
移除了面向 `solidworks-plan-drawing-views` 的 prompt，因此实际仍为六项工具、零 prompts。生产 profile
从未改写的历史 `baseline` 包切换至新的不可变 `native-v3` 3.0.0 包；prompt-pack 合同升至 2.0，prompt
request/envelope 升至 3.0，并删除 `mcp_tools` 模式、`allowed_mcp_tools` 及外部验证/执行动词。PlannerEngine
现在只通过仓库 MCP Sampling 的不可执行 `submit_solidworks_view_plan` 工具接收一个候选，后续确定性校验、
能力评估、原子发布及 C# 事务全部由仓库拥有。契约测试锁定 production profile、活动 pack Schema、桥文件
不存在、默认 prompts 为空，以及生产 Python/prompt 中无 Skill、CLI 或外部执行器标记；外部 planning Skill
和 `view-plan.contract.json` 中的来源记录仅保留为设计/溯源资料。DrawingPlan 1.0 兼容链由 D4 随后迁出。

D4 已完成 DrawingPlan 1.0 显式兼容入口。旧三项 helper 已从默认 `adapters/claude/server.py` 及其导入中
彻底移除，新增独立 `drawing_plan_compat_server.py`（FastMCP 1.0.0）且只注册
`validate_part_drawing_plan`、`create_part_drawing`、`verify_part_drawing`。三工具均发布完整结构化
DrawingPlan 1.0 Schema；创建和核验分别只调用私有 C# `execute_drawing_plan` 与 `verify_drawing_plan`，
独立进程在 state-version 不匹配时从执行服务重同步后重试一次，不翻译 ViewPlan 1.4。兼容服务器拥有独立
机器合同和 PowerShell 启动器，但不加入默认 `.codex/config.toml`；契约测试锁定三工具集合、1.0 Schema、
默认六工具面隔离、零 prompts、显式启动器、私有事务路由及状态重同步。至此 D1-D4 默认链切换全部完成。

E0 已完成发布候选收口。最终统一矩阵在全新目录执行六个固定案例，offline/integration/live 6/6 通过；
其中 SolidWorks 2025 SP5（revision `33.5.0`）实机矩阵再次完成 13/13，所有新工程图和审计侧车均位于
发布候选制品目录。独立 DrawingPlan 1.0 兼容 MCP 随后通过真实 stdio 协议发现且仅发现三项工具，并按
validate/create/verify 顺序完成四视图工程图事务；执行状态严格为 `0 -> 1 -> 1`，创建后的只读独立核验
通过。为避免真实保存/关闭/重开事务误用普通 30 秒 HTTP 超时，DrawingPlan 创建与核验现与 ViewPlan
事务共同使用长事务超时并由测试锁定。两轮实机验证前后 `validation/` 四项输入的树哈希均保持
`0638a043ab5bcec518a6437f879b4705f33fa0ad36b25676f4e34b47aa759d7e`。完整证据和复现命令见
`docs/E0_RELEASE_CANDIDATE_REPORT.md`。
