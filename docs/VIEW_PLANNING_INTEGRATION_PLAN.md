# 仓库原生 SolidWorks 单零件工程图开发计划

状态：E0-E4 当前三 Skill 生产链发布候选已完成；F0-F3、F6 已完成；F4-F5 原生执行候选已实现、待 F7 完整能力矩阵晋级；G0-G7 已完成，最终布局八项操作与六项安全能力已由 SolidWorks 2025 SP5 完整矩阵晋级
最后更新：2026-08-15
目标协议：`solidworks-view-plan` schema 1.4；后续 `solidworks-dimension-plan` 1.0；后续
`solidworks-drawing-layout-plan` 1.0

## 1. 目标

将主视图选择、最小视图集、剖视/断面/局部/辅助视图决策、特征覆盖、尺寸标注和最终工程图
排版建设为本仓库的正式业务能力。旧 `$solidworks-plan-drawing-views` 仅作为历史工程规则和契约
设计来源，不作为生产运行时依赖；当前仓库自有 Codex Skills 是用户入口和显式上层编排器。

生产调用链必须保持：

```text
User / Codex
  -> bootstrap-solidworks-host
  -> solidworks-initialize-drawing-handoff
  -> solidworks-create-drawing-views
  -> [F] solidworks-dimension-drawing
  -> [G] solidworks-finalize-drawing-layout
  -> repository semantic MCP (adapters/codex/server.py -> adapters/claude/server.py)
  -> repository execution_client
  -> repository C# Execution Service
  -> private atomic SolidWorks COM operations
  -> save / close / read-only reopen / independent verify
```

当前版本以前三个 Skill 为生产入口。完成 F/G 后扩展为五 Skill；每个下游阶段读取并哈希绑定上游
不可变计划、工程图和核验侧车，再创建新的后继 `.SLDDRW`，不得原地修改已验收的上游图纸。

## 2. 不可违反的边界

- Agent 只能调用工程语义级工具；COM 方法、选择动作、视图创建步骤、保存步骤不得暴露。
- Python 规划层不调用 SolidWorks COM；C# Execution Service 是唯一 COM 边界。
- 模型输出始终是不可信候选，必须依次通过输入完整性、JSON Schema、工程语义、特征
  覆盖、布局和执行能力校验。
- 工程正确性与当前执行能力分离。执行器缺少能力时返回 `capability_blocked`，不得删除
  视图、替换视图类型、使用隐藏线代替剖视或修改冻结几何。
- 创建类操作必须使用新输出路径，执行事务必须保存、关闭、只读重开并核验持久化结果。
- ViewPlan、DimensionPlan 和 DrawingLayoutPlan 必须是独立、严格、不可变、可哈希绑定的协议；不得
  把后续尺寸和最终排版字段无边界地塞入 ViewPlan 1.4，也不得翻译到 DrawingPlan 1.0。
- 尺寸值、公差、配合、螺纹等级、形位公差和表面要求必须来自受信模型数据或用户批准输入；不得从
  PNG、屏幕像素或图纸比例推测制造要求。几何测量值只能按协议显式标记为参考尺寸。
- 最终排版只能改变允许对象的位置、对齐和批准的比例，不得改变尺寸语义、视图覆盖、配置、显示状态、
  投影法、剖切定义或模型关联。
- `validation/` 是验证数据，只读使用，不得由开发测试覆盖或清理。

## 3. 当前语义工具面

| 工具 | 责任 | SolidWorks 写入 |
|---|---|---:|
| `solidworks_status` | 报告 MCP、Planner、执行器和 SolidWorks 状态 | 可选启动 |
| `inspect_solidworks_host` | 仓库自有的 no-launch 主机、安装、注册和文件系统预检 | 否 |
| `bootstrap_solidworks_host` | 仓库自有的隔离有界 COM 激活验证；注册修复必须显式授权 | 可选启动/修复 |
| `inspect_part_for_drawing` | 只读检查零件、配置、本地化标准视图名和包围盒 | 否 |
| `initialize_part_drawing_handoff` | C# 事务生成空白图纸、两份报告、六视图图片及 manifest-last 冻结交接 | 是 |
| `plan_part_drawing_views` | 调用 PlannerEngine，发布通过确定性校验的冻结计划 | 否 |
| `publish_validated_part_drawing_view_plan` | 重校验并原子发布显式上层 Skill 生成的单个完整候选 | 否 |
| `validate_part_drawing_view_plan` | 校验导入/生成计划及当前执行能力 | 否 |
| `create_part_drawing_from_view_plan` | 事务执行完整冻结计划 | 是 |
| `verify_part_drawing_view_plan` | 对已有工程图进行独立只读核验 | 否 |
| `initialize_part_drawing_dimension_handoff` | 冻结已核验视图图纸、尺寸/PMI/特征/持久引用及批准输入 | 否 |
| `publish_validated_part_drawing_dimension_plan` | 重校验并原子发布唯一完整 DimensionPlan 候选 | 否 |
| `validate_part_drawing_dimension_plan` | 绑定已发布计划/请求并运行 Python 与 C# COM-free 门禁 | 否 |
| `qualify_dimensioned_part_drawing` | F7 实机矩阵专用创建，不晋级生产能力 | 是 |
| `verify_qualified_dimensioned_part_drawing` | F7 实机矩阵专用独立核验 | 否 |
| `create_dimensioned_part_drawing` | 能力受支持时事务创建新的尺寸图纸和侧车 | 是 |
| `verify_dimensioned_part_drawing` | 对尺寸图纸进行独立只读重开核验 | 否 |
| `initialize_part_drawing_layout_handoff` | 绑定尺寸请求、计划、已核验图纸/侧车与 G0 实证，冻结精确布局边界 | 否 |
| `publish_validated_part_drawing_layout_plan` | 从唯一不可变 LayoutPlanningRequest 确定性求解并原子发布布局计划 | 否 |
| `validate_part_drawing_layout_plan` | 重算唯一计划并运行 Python 与 C# COM-free 布局门禁 | 否 |
| `qualify_final_part_drawing` | G7 矩阵绑定的资格创建；允许 planned 操作但不绕过 unsupported G0 边界 | 是 |
| `verify_qualified_final_part_drawing` | G7 矩阵绑定的独立资格核验，不晋级生产目录 | 否 |
| `create_final_part_drawing` | 能力受支持时事务创建新的最终图纸和布局侧车 | 是 |
| `verify_final_part_drawing` | 对最终图纸进行独立只读重开和布局指纹核验 | 否 |

默认 MCP 当前共二十四项工程语义工具、零 prompts。`.codex/config.toml`、FastMCP 实际发现结果和
`semantic-tools.schema.json` 必须保持精确一致；五个仓库自有 Skill 只能调用各自 allow-list 内的上述工具。
`plan_part_drawing_views` 与显式 Skill + `publish_validated_part_drawing_view_plan` 是互斥规划分支；当前
Codex 生产流程默认使用显式 Skill 分支，只有用户明确要求 MCP Sampling 时才使用 PlannerEngine 分支。

旧 `DrawingPlan` 1.0 已移出默认工具面，并通过独立三工具 MCP 接入显式兼容入口；C# 私有事务继续复用。

### 3.1 F/G 计划新增的语义工具

尺寸阶段已在 F6 新增：

- `initialize_part_drawing_dimension_handoff`
- `publish_validated_part_drawing_dimension_plan`
- `validate_part_drawing_dimension_plan`
- `create_dimensioned_part_drawing`
- `verify_dimensioned_part_drawing`

最终排版阶段已在 G6 新增：

- `initialize_part_drawing_layout_handoff`
- `publish_validated_part_drawing_layout_plan`
- `validate_part_drawing_layout_plan`
- `create_final_part_drawing`
- `verify_final_part_drawing`

新增工具只能是工程语义级完整事务。旧 `auto_dimension_drawing`、`add_drawing_dimension` 等 legacy 动词
只可用于 API 调研，不得直接注册到默认生产面或作为新协议执行后端。

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

dimension_planner/             [F]
  planning_models.py
  plan_store.py
  validators/
  contracts/
    dimension-plan.schema.json
    dimension-planning-request.schema.json
    dimension-planning-result.schema.json
    dimension-executor-capabilities.schema.json
  capabilities/
    current.json

drawing_layout_planner/        [G]
  planning_models.py
  constraint_engine.py
  plan_store.py
  validators/
  contracts/
    drawing-layout-plan.schema.json
    drawing-layout-request.schema.json
    drawing-layout-result.schema.json
    drawing-layout-executor-capabilities.schema.json
  capabilities/
    current.json
```

F/G 可在实现阶段按仓库复用情况共享基础 canonical JSON、哈希、验证结果和原子发布组件，但三个业务协议、
能力清单和验证入口必须保持明确分离，不能以复用为由将尺寸或最终排版静默并入 ViewPlan 执行链。

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

### E. 当前三 Skill 生产链发布收口

- [x] E0：底层执行基线收口（历史发布候选）。
  - [x] 基于 D4 最终代码重跑 offline/integration/live 六案例统一矩阵。
  - [x] SolidWorks 2025 SP5 全能力 live 矩阵 13/13 通过。
  - [x] 通过独立 stdio MCP 完成 DrawingPlan 1.0 validate/create/verify 实机事务。
  - [x] `validation/` 四项输入在全部发布候选验证前后 SHA-256 完全不变。
  - [x] 发布候选报告、README 和 CHANGELOG 与最终证据同步。
- [x] E1：冻结当前三 Skill 的生产链合同。
  - [x] 固化 `bootstrap-solidworks-host` → `solidworks-initialize-drawing-handoff` →
    `solidworks-create-drawing-views` 的顺序、输入、输出、状态变化和停止条件。
  - [x] 固化默认十工具语义面、各 Skill allow-list 和零 prompts；三者与 Codex 配置及 MCP Schema 同步。
  - [x] 显式 Skill publish 分支作为当前 Codex 默认路径；PlannerEngine Sampling 分支仅在用户明确要求时使用，
    且一次任务两条分支互斥、只允许一个候选和一次发布。
  - [x] initializer 返回的完整 `planning_request` 在 publish/validate/create/verify 间原样复用。
- [x] E2：建立 Skill/MCP 边界合同测试。
  - [x] 校验三个 `SKILL.md` 的 front matter、引用文件、工具 allow-list 和仓库权威合同。
  - [x] 拒绝私有 executor 动词、原始 HTTP、第二 MCP client、Python COM、UI 自动化和 legacy 桥。
  - [x] 校验发布后计划不可修改或覆盖；`capability_blocked` 可发布但不可执行。
  - [x] 对 `.codex/config.toml`、FastMCP 实际发现结果和 `semantic-tools.schema.json` 执行三方漂移检查。
- [x] E3：完成真实用户入口端到端矩阵。
  - [x] 经仓库自有主机预检 Skill、initializer Skill、视图 Skill 和真实 stdio MCP 到 C# COM 事务运行。
  - [x] 初始化一次、候选一个、发布一次；create 后独立 verify，且不存在私有操作泄漏。
  - [x] 覆盖主机阻塞、handoff 哈希漂移、计划拒绝、能力阻塞、路径碰撞和核验不一致等负向案例。
  - [x] `validation/`、源模型、模板和全部已发布上游制品在运行前后保持不变。
- [x] E4：冻结当前三 Skill 链的新发布候选证据。
  - [x] 记录 Git commit、三个 Skill、Schema、`native-v4`、能力清单、C# runtime 和全部制品 SHA-256。
  - [x] 更新 README、CHANGELOG、复现命令和发布报告；DrawingPlan 1.0 仅作为兼容性附加门禁。

E0 的 45 项 C# 合同和 13 项 SolidWorks 实机矩阵继续作为底层回归基线，但不能单独证明当前三 Skill
用户入口已完成发布验收；E1-E4 全部完成后，才可恢复“当前版本发布候选完成”的结论。

### F. 尺寸标注

尺寸阶段使用独立 `solidworks-dimension-plan` 1.0，并从已核验的视图图纸创建新的后继工程图；当前
ViewPlan 的 `dimension_zones` 仅表示预留空间，不是尺寸语义、尺寸创建结果或最终排版结果。

F0 已建立 COM-free 探测请求/实证报告合同、固定十四项能力目录、确定性证据评估器、独立 C#
尺寸合同套件和仓库 Execution Service 内部 live 探测链，并支持哈希绑定的研究模型/工程图语料对
与生产冻结上游两类显式来源；详见 `docs/F0_DIMENSION_API_SCOPE.md`。在 SolidWorks 2025 SP5
四组研究语料矩阵已覆盖线性、直径、半径、角度、孔标注、倒角、非零公差和前后缀，并证明 70 个
显示尺寸跨完全关闭/只读重开的规范化稳定身份和值；无选择稳定失败案例也已完成。47 个含附件记录的
79/79 个有效原始持久引用在只读重开后解析成功，另有两个 `type=0/entity=null` COM 占位槽被显式记录
并排除。十四项研究覆盖均为 `covered`，精确文字边界按实证判定为 `unsupported`。随后确认字节级
复制 initializer 会使 SolidWorks 对副本 `Save3(errors=1)`；B3 事务改用原生
`ISldWorks.CopyDocument`，在无打开文档的明确前置条件下保持 initializer 哈希不变并成功创建、
保存、只读重开和独立核验四个模型关联视图。最终 build18 矩阵包含四个研究案例和一个生产冻结案例，
5/5 为 `evidence_ready`、`production_frozen_case_count=1`、`overall_status=complete`；冻结图纸导入
18 个模型尺寸并通过保存重开证据门禁。十三项能力保持 `planned`，`annotation_text_bounds` 为
`unsupported`，未提前提升能力状态。

- [x] F0：冻结尺寸范围并完成 SolidWorks 2025 SP5 原生 API 实证。
  - [x] 验证模型尺寸导入、`IDisplayDimension` 创建/遍历、附着实体、位置、文字边界和保存重开稳定性。
  - [x] 对线性、直径、半径、角度、孔标注、倒角、公差和前后缀逐项判定 `supported/planned/unsupported`。
  - [x] 确定跨保存重开的稳定尺寸身份和附着实体持久引用；不可靠能力保持 `capability_blocked`。
- [x] F1：实现不可变尺寸规划 handoff。
  - [x] C# 只读冻结上游 ViewPlan/图纸/侧车哈希、实际投影几何、模型驱动尺寸、PMI、孔槽阵列、持久引用、
    视图边界、现有注释边界和 `dimension_zones`。
  - [x] 尺寸来源按“模型/PMI和已批准数据 → 用户确认输入 → 仅参考的几何测量值”分级并记录溯源。
  - [x] 发布 `dimension-planning-handoff.json` last；源模型及上游图纸不得变脏或被覆盖。

F1 已新增严格的 `dimension-planning-handoff-request.schema.json` 和
`dimension-planning-handoff.schema.json`、Python 请求构造/发布物复核器、独立 C# 请求合同，以及不进入
默认 MCP 工具面的私有 `POST /api/dimension-planning/handoff` 只读事务。事务在 COM 前核对 ViewPlan、
独立核验工程图和侧车绑定，在只读会话内读取计划视图边界、普通边及轮廓边投影几何、模型尺寸、模型
注释、孔/槽/线性与圆周阵列、持久引用、现有注释保守显示包络和 `dimension_zones`；精确文字边界继续
显式记录为 `unsupported_exact`。普通实体引用与轮廓边背靠面引用分域记录，几何测量值固定标记为
`reference_geometry_measurement` 且不得作为制造要求；用户确认输入必须包含显式批准人、时间、批准引用
和严格值类型。

SolidWorks 2025 SP5（revision `33.5.0`）冻结 ViewPlan 实机候选已完成：4 个计划视图读取 13 个投影实体
（1 条普通直线、3 条普通圆弧、9 条轮廓直线）、4 个现有注释、27 个模型驱动尺寸、2 个制造特征
（孔和圆周阵列）及 13 个仅参考测量值；该零件没有模型 PMI，因此 `pmi_annotations` 为空数组而非推断值。
发布物 SHA-256 为 `405e50726aadbd107d535592dd360d83acbd195ad455d15e5132bfa4063e907e`，四项
上游制品前后哈希完全一致，源文档 dirty flag 为 false，工程图以只读方式打开，发布后会话正常退出。
F7 仍负责多类零件的完整尺寸执行矩阵；本次 F1 实机候选只验收不可变 handoff，不提前提升 F0 能力
清单，也不引入 DimensionPlan、尺寸门禁或尺寸创建事务。
- [x] F2：加入 DimensionPlan 1.0 Schema、领域模型、能力清单和原子 PlanStore。
  - [x] 首批合同覆盖线性、对齐、直径、半径、角度、参考尺寸、孔径/孔深/数量、孔距/孔组定位、
    总体尺寸、台阶、凸台、槽、倒角、圆角和对称尺寸。
  - [x] 每个尺寸绑定来源、目标视图、附着实体、特征、数值模式、显示格式、尺寸区、层级和核验公差。
  - [x] 形位公差、基准、粗糙度、焊接符号和无来源制造要求不属于 DimensionPlan 1.0 首版。

F2 已冻结 `dimension-plan`、`dimension-planning-request/result` 和
`dimension-executor-capabilities` 四项 Draft 2020-12 合同及对应严格、不可变领域模型。尺寸来源限定为
模型/PMI、已批准用户输入和仅参考几何测量三层；参考测量只能形成参考层级尺寸且不能携带制造公差。
版本化能力注册表逐项覆盖 18 类尺寸和 7 项共享执行元素，当前真实地将尚未实现的执行路径评估为
`capability_blocked`；工程有效性将在 F3 独立判定。`PlanStore` 仅向既有发布目录原子创建
`dimension_plan.json`，拒绝覆盖冻结计划及写入 `validation/`。
- [x] F3：实现尺寸确定性门禁。
  - [x] 固定 `integrity → schema → source → attachment → semantics → coverage → redundancy → layout → capability` 顺序。
  - [x] 拒绝重复或冲突尺寸、不允许的封闭尺寸链、不稳定引出位置、不可见附着实体及编造的公差/配合。
  - [x] 工程上有效但执行器不支持的计划允许发布为 `capability_blocked`，创建事务必须拒绝。

F3 已实现严格失败短路的九段仓库验证链。完整性门禁重新读取并校验 F1 handoff、四项上游制品和
DimensionPlan 的路径/哈希/配置绑定；来源门禁只接受模型尺寸、明确批准输入或仅参考测量中的实际值，
并要求公差上下限或配合文本逐项存在于批准输入。附着门禁验证目标视图可见实体、持久引用、特征和
参考测量实体集合；其后门禁固定检查领域语义、制造特征/批准输入覆盖、重复或冲突尺寸、封闭尺寸链、
尺寸区和冻结初始位置。`DimensionPlannerEngine` 仅在八项工程门禁通过后评估能力并原子发布；当前
计划会如实发布为 `capability_blocked`，`require_supported` 为 F4 创建入口提供稳定的强制拒绝边界。
- [ ] F4：实现 C# 原生尺寸 MVP 和无覆盖事务。
  - [ ] 支持线性、直径、半径、角度、参考尺寸、基本孔标注、前后缀、数量、精度和冻结初始位置。
  - [ ] 从上游图纸复制到事务临时文件，创建后重建、内存回读、保存、关闭、只读重开并原子提交新图纸/侧车。
  - [ ] 精确核验尺寸身份、数值、类型、目标视图、附着实体、文字和位置；禁止未计划尺寸和部分提交。

F4 已加入哈希锁定的独立 C# DimensionPlan 合同、MVP 编译器、handoff/五项上游制品预检和私有
validate/execute 入口；不会进入默认 semantic MCP，也不会翻译到 DrawingPlan 1.0。原生执行候选使用
`ISldWorks.CopyDocument` 创建同目录临时图纸，支持模型尺寸导入过滤及明确附件的线性、直径、半径、
角度、参考尺寸和基本孔标注创建，写入前后缀、精度与冻结初始位置，并按“内存回读 → Save3 → 关闭 →
只读重开 → 精确回读 → 新图纸/侧车双文件提交”执行。任何异常都会清理临时/部分输出；源模型、上游图纸、
handoff、ViewPlan、原侧车和已发布 DimensionPlan 在事务前后均重新哈希。生产创建入口额外读取版本化能力
清单，只有尺寸类型和所需元素都具有 `supported + live + evidence_sha256` 时才允许接触 COM；当前状态保持
`capability_blocked`，因此上述 F4 验收项在真实 SolidWorks 创建/保存重开证据发布前不提前勾选。
- [ ] F5：补齐高级尺寸能力。
  - [ ] 沉孔、沉头、锪平孔、盲孔、螺纹、槽/键槽、组合倒角/圆角、基线尺寸、坐标尺寸和阵列标注。
  - [ ] 上下偏差、极限尺寸和配合代号仅在受信输入存在时执行，并完成原生格式与持久化回读。

F5 在不改变已冻结 DimensionPlan 1.0 的前提下补齐其 18 类尺寸编译联合。沉孔/沉头/锪平孔、盲孔和
螺纹继续由模型关联的原生孔标注生成，并在内存与只读重开阶段冻结完整逻辑文字及 hole-callout variables；
槽/键槽、孔距/孔组定位、总体/台阶/凸台、组合倒角/圆角、对称和对齐尺寸分别走明确附件及对应原生 API。
`hierarchy.baseline_id` 的非坐标组必须至少包含两个尺寸并共享同一冻结基准附件；孔组定位可编译为按冻结
投影几何确定方向的原生 ordinate，`chain_id` 则写入并回读链式显示状态。上下偏差/单向/极限公差逐值匹配
approved quantity，配合代号逐字匹配 approved exact text，并根据冻结制造特征选择 hole/shaft fit 后才调用
`IDimensionTolerance`；侧车回读公差类型、上下值和 hole/shaft fit。当前仍无 F5 真实图纸保存重开矩阵证据，
所以能力清单继续保持 `planned`，两项 F5 验收框不提前勾选。
- [x] F6：新增 `solidworks-dimension-drawing` Skill 和五项尺寸语义工具。
  - [x] Skill 只生成一个完整候选并编排 publish/validate/create/verify，不写盘、不调用 legacy 工具或 COM。
  - [x] 所有阶段使用同一不可变 DimensionPlan/request；发布后不得修补或覆盖。

F6 已将默认工程语义 MCP 扩展为 15 项工具，并新增尺寸 handoff 初始化、原子发布、COM-free 验证、
事务创建和独立核验五个公开工程动词；私有 C# validate/execute/verify 动词仍只存在于 Execution Service。
`solidworks-dimension-drawing` Skill 读取仓库版本化 `native-v1` prompt pack，只在内存中生成一个候选，
并强制从初始化返回值原样传递同一 `DimensionPlanningRequest`、计划对象和输出路径。已发布计划按磁盘对象和
文件 SHA-256 再绑定，禁止覆盖或发布后修补。新增独立 C# 核验器会在 COM 前重验计划、图纸、尺寸侧车和
六项冻结输入，再只读重开图纸核对尺寸身份、数量、附件、文本、孔标注变量、公差和持久化指纹；核验不保存
文档且不递增状态版本。能力清单仍按真实证据保持 `planned/unsupported`，因此工程有效计划可以发布为
`capability_blocked`，但 F7 提升全部所需能力之前 create/verify 会稳定拒绝。
- [ ] F7：完成尺寸离线、合同和真实 SolidWorks 矩阵。
  - [ ] 覆盖板类、轴套类、支架类、法兰类、槽腔类和螺纹零件。
  - [ ] 验收无悬空、重复、未计划尺寸；源模型和上游图纸不变，保存重开规范化指纹一致。

F7 已完成离线证据门禁及首次资格事务候选：新增不可变矩阵请求、单案例证据和汇总三项 Schema，固定六类
零件、DimensionPlan 1.0 全部 18 类尺寸及六项生产执行元素的覆盖要求。默认 MCP 增加两项仅供 F7 实证的
`qualify_dimensioned_part_drawing` / `verify_qualified_dimensioned_part_drawing` 工程语义工具；它们要求计划、
原始请求、输出路径和矩阵案例逐项哈希绑定，只允许 `planned` 能力取证，已知 `unsupported` 仍在 COM 前
拒绝，并且绝不修改能力清单。生产 `create/verify` 的 `supported + live + evidence_sha256` 门禁保持不变。

F7 完整正向矩阵准备链现已补齐。新增高级 evidence-bound recipe `1.1`，可从不可变 handoff 精确表达
`model_or_pmi`、`user_confirmed_input` 和非制造 `reference_geometry_measurement` 三类来源，以及用户批准的
数值/配合公差和精确前后缀；所有值仍须通过仓库来源、附件、语义、覆盖、冗余和布局门禁。新增
`dimension-f7-preparation-request` 合同、`dimension_planner/f7_preparation.py` 和
`scripts/prepare_dimension_f7_live_matrix.py`：在 COM 前要求六个独立 handoff、至少五个不同源模型、18 类尺寸
恰好各一次及六项执行元素全部有正向计划覆盖，先完成全部候选门禁，再每 handoff 发布一个不可变计划，最后发布
矩阵请求。F7 live runner 同步到当前 24 工具/零 prompt 默认面；矩阵请求若缺类型/能力、复用代理 handoff、
请求/计划不连续或源制品哈希漂移，会在连接 SolidWorks 前拒绝。C# 合同现逐项编译并资格预检 18 类联合，并用
受信容差/前后缀计划覆盖六项通用执行能力。完整设计和运行命令见
`docs/F7_COMPLETE_DIMENSION_MATRIX.md`。上述代码完成不代表实机晋级；仍需用六类真实 handoff 生成并跑完新的
18 类保存重开/独立核验矩阵后，才能评审能力候选并勾选 F7。
矩阵运行器对每个案例执行 `validate(capability_blocked) → qualify → independent qualification verify`，逐阶段
核对 canonical/request 哈希，并在 C# 事务侧车已通过内存回读、保存关闭、只读重开及独立核验后才原子
发布案例证据。汇总器重新
哈希计划、输出、侧车和全部冻结上游输入，拒绝悬空、重复、未计划或身份/指纹不一致；只有六类、18 类
尺寸和所需元素全部具有真实证据时，才允许生成单独的 `0.4.0/0.3.0` 能力清单晋级候选，且不会自动覆盖
`current.json`。当前仓库仅有 4 组 F0 模型/图纸语料、1 组 F1 尺寸 handoff，尚无覆盖六类零件的已发布
DimensionPlan，因此完整真实 SolidWorks 矩阵及 F4/F5 勾选仍保持未完成；能力清单继续为
`planned/unsupported`，生产创建继续失败关闭。

F7 首个固定环资格案例现已在 SolidWorks 2025 SP5 完成原生创建和独立新进程只读核验：直径值、持久引用、
保存关闭/重开指纹及源模型丢弃后干净重开均通过。所有默认工程语义 COM 事务改由后台 C# Execution Service
在确定性门禁之后按需启动 SolidWorks，并在每个事务后只退出其拥有的会话；PID 与进程启动时间通过本机
所有权租约跨 Execution Service 重启核验。任务自有文档无法正常关闭时，只有精确所有权或显式只读恢复集
门禁通过后才允许有界进程兜底。资格创建 PID 和独立核验 PID 均在返回前确认 `remaining_process_ids=[]`。
该单案例证据不替代五个真实零件的六类完整矩阵，也不触发能力晋级。

为先建立可调优基线，仓库现提供证据绑定的 first-draft 生成器及固定环代理配置。它已从现有唯一完整 F1
handoff 生成六份独立 candidate，按三类尺寸/份覆盖全部 18 类 DimensionPlan 联合；每份均通过完整性、
Schema、来源、附着、语义、覆盖、冗余和布局八项工程门禁。汇总制品强制记录
`category_evidence=proxy`、`eligible_for_f7_promotion=false`，候选文件也不使用生产发布名
`dimension_plan.json`。因此这批输出可用于后续效果调优和真实零件绑定替换，但不计作六类真实 SolidWorks
证据，也不解除 `capability_blocked`。

五个真实零件现已完成六类实机验收矩阵：板、C 型夹、支撑板、固定环和 `ACCCMD-12/12345`
分别覆盖 plate、bracket、threaded、shaft_sleeve、flange、slot_cavity；每个案例均完成资格创建、
保存关闭/只读重开和独立核验，并发布图纸、验证侧车和案例证据。该结果满足“五个真实零件可顺利完成
标注”的开发验收口径，但矩阵当前只包含 linear/diameter 两类尺寸，未达到 F7 正式能力晋级所需的全部
18 类尺寸覆盖。因此能力清单仍不晋级，`current.json` 保持不变，正式 F7 promotion 继续失败关闭。

### G. 最终工程图排版布局

G 必须在 F 的真实尺寸执行后运行。最终排版依据 SolidWorks 重建后读取的实际视图、尺寸文字、箭头、
界线、引线、标签和中心元素边界，不得仅凭 ViewPlan 的计划框或尺寸带猜测最终坐标。
G 只负责已有视图、尺寸和注释的最终空间整理；标题栏内容填写、技术要求编制、形位公差/基准/粗糙度
创建以及 PDF/DWG/DXF 导出不在本阶段范围内。

- [x] G0：验证真实注释边界和重建漂移。
  - [x] 实测视图轮廓、尺寸文字/线/箭头、孔标注/引线、标签、剖切符号、中心元素、图框和标题栏边界。
  - [x] 记录重建前后及保存重开漂移；原生 API 无可靠精确文字边界时以实机稳定性证据显式标记 `unsupported`，不把确定性近似冒充 exact-supported。

G0 已建立第一批仓库原生开发基础：新增严格的 `layout-boundary-probe` / `layout-boundary-evidence`
Draft 2020-12 合同、固定十一项能力目录、COM-free 确定性证据评估器和独立 C# 合同套件。Execution Service
新增不进入默认 semantic MCP 的私有只读研究入口；它接收哈希绑定的已核验尺寸图、已核验 ViewPlan 图和
隔离标题栏夹具，在不保存图纸的前提下发布重建前、重建后及完全关闭后只读重开的三份对象边界快照，并最后发布
证据。原生读回当前覆盖视图轮廓、尺寸显示图元、note extent、引线点、仓库管理辅助标签、原生详图标签、
剖切 line/arrow/text info、中心元素、图框和标题栏；任何文字宽度近似均明确保持 `planned`。完整范围和后续
门禁见 `docs/G0_LAYOUT_BOUNDARY_SCOPE.md`。

2026-08-14 已完成第一轮固定六类 G0 实机矩阵。请求从 F7 `matrix-live-r14` 的 plate、bracket、threaded、
shaft_sleeve、flange、slot_cavity 六类已独立核验尺寸图纸生成，并新增不可变矩阵请求/汇总合同、一次性发布器、
六类聚合门禁和磁盘证据一致性复核。六类视图轮廓、图框以及重建/只读重开漂移均已覆盖；threaded、
shaft_sleeve、flange 还覆盖普通 note 和引线。三条 note 经原生身份字段确认均为带引线的 `M8/M16 螺纹孔`
孔标注，不是视图标签。尺寸显示六类均可观测且零漂移，但文字宽度仍需确定性近似，因此只记为 `partial`。
矩阵没有 detail/section/auxiliary 标签、剖切符号、中心元素或原生标题栏正向对象，所以这些能力保持 missing，
不得推断为 unsupported。

实机复测还纠正了两项原生数组解析：`INote.GetExtent` 按两个 XYZ 角点解释，`IDisplayData.GetLineAtIndex3`
跳过四项样式元数据后读取两个 XYZ 端点；箭头按 tip/direction/width/height 解释，剖切 line/arrow/text 分别按
二维点对、XYZ 点组和文字原点处理。修正后所有边界均回到 A3 图纸尺度，第三轮矩阵总体仍为 `incomplete`、
无 blocker，汇总文件 SHA-256 为 `41027d079f992fb26a044fe670a39fef6bb865145ceda64b667a4f349e587d32`。
2026-08-14 已完成 G0 最终资格。新增哈希绑定的已核验 ViewPlan 来源和隔离的标题栏研究夹具来源；生产
ViewPlan 事务创建并独立只读核验了辅助视图显式标签、中心标记、水平/垂直中心线和全剖切割符号，隔离 C#
夹具通过 `ISheet.InsertTitleBlock` 创建标题栏并以 `ITitleBlock.GetExtents` 在保存重开前后精确读回。最终资格
报告 SHA-256 为 `292825190b481261bb37f9e6b6154d01be2917e7887a2745ddc1aae7e42f79a4`，十一项均已资格化：九项
`supported`，尺寸显示和剖切符号因 SolidWorks 未暴露精确文字 glyph extent 明确为 `unsupported`；能力目录
已晋级到 `1.0.0` / `live_complete`。G0 完成，下一阶段从 G1 不可变布局 handoff 开始。
- [x] G1：实现不可变布局 handoff。
  - [x] 冻结上游 DimensionPlan、尺寸图纸和侧车哈希，以及全部实际对象边界、约束、锁定区和最小间距。
  - [x] 发布 `drawing-layout-handoff.json` last；不得改变上游尺寸数量、数值、附着关系或图纸文件。
2026-08-15 已完成 G1。新增严格 request/handoff Draft 2020-12 Schema、Python 哈希与语义连续性门禁、私有 C#
只读事务和 `initialize_part_drawing_layout_handoff` 工程语义入口。事务对真实 F7 尺寸图执行关闭、只读打开、
重建、关闭、只读重开，冻结尺寸 ID/数值/模型持久引用、全部实际边界、视图位置锁、投影父子约束、图框/标题栏
锁定区和三类最小间距，并在五项上游哈希复核后最后发布唯一 handoff。真实 plate 候选包含 5 个边界对象和 1 个
尺寸，三次对象快照哈希一致，上游哈希全部不变；因 `dimension_display_bounds` 的精确 glyph extent 已在 G0
资格中明确为 unsupported，handoff 如实发布为 `capability_blocked`，该状态不影响 G1 交接完成但会阻止后续不安全
执行。候选 SHA-256 为 `8f3d6d0346ad8ad1d48b63a847e0dd7c4cc630d0750e6cf3e0f849b4085ad672`。
- [x] G2：加入 DrawingLayoutPlan 1.0 Schema、领域模型、能力清单和原子 PlanStore。
  - [x] 计划可移动尺寸/注释/允许的视图，调整尺寸层级、引线、局部比例、主比例及经授权的图幅。
  - [x] 计划不得删除必要内容、改变制造语义、配置/显示状态/投影法、剖切定义、模型关联或冻结几何。
2026-08-15 已完成 G2。新增独立、严格、深度不可变的 `DrawingLayoutPlan 1.0` Draft 2020-12
Schema 和 Pydantic 领域模型，只允许八类排版操作：移动尺寸、移动注释、路由引线、移动经授权视图、设置尺寸
层级、设置经授权局部比例、设置经授权主比例及设置与审批记录精确一致的图幅。计划冻结 G1 handoff、
DimensionPlan、尺寸图、验证侧车、配置、尺寸语义快照、对象清单、锁定对象和必需 G0 边界能力；删除对象、新增
制造注释、源模型写入、上游图纸覆盖、部分提交以及弱化尺寸/视图/模型关联不变量在协议层无合法表达。
新增独立 `plan-current.json` 能力清单并按协议、版本和文件 SHA-256 绑定 G0 `current.json`，同时区分八项原生
操作能力、六项安全回读能力和计划要求的边界能力。G4/G5 实证前全部执行能力保持 `planned`，G0 已知不支持的
精确尺寸文字边界继续产生 `boundary.dimension_display_bounds` 阻断。原子 PlanStore 只向已存在且非
`validation/` 的目录一次性发布 `drawing_layout_plan.json`，同目录落盘、禁止覆盖和并发抢占；详细合同见
`docs/G2_DRAWING_LAYOUT_PLAN.md`。
- [x] G3：实现仓库确定性排版引擎和门禁。
  - [x] 使用规则加约束求解；Skill/模型表达优先级和布局意图，合法最终坐标由仓库引擎求解。
  - [x] 固定调整顺序：尺寸文字/层级 → 引线/标签 → 可移动视图 → 局部比例 → 主比例 → 授权图幅。
  - [x] 校验安全区、标题栏/保留区、对象碰撞、尺寸穿越、投影对齐、最小间距、字体和箭头可读性。
2026-08-15 已完成 G3。新增严格的 `drawing-layout-planning-request` 合同和不可变意图/结果模型，调用层只可
表达对象优先级、偏好位置、比例候选和已批准图幅候选；核心网格、搜索上限、可读性阈值、六阶段顺序及操作阶段
映射冻结在仓库 `deterministic-layout-v1` 规则集中，并按文件 SHA-256 写入计划 provenance。确定性求解器按
优先级和稳定 ID 排序，在 1 mm 网格上执行有界邻域搜索、正交引线路由、投影轴约束、局部/主比例候选选择和授权
图幅匹配，只生成 G2 八类合法操作。引擎在求解前复核 G1 handoff 及五项上游文件哈希、G0 清单版本和字节哈希；
`capability_blocked`、不精确边界、尺寸对象缺少明确 dimension ID/附着点均提前失败。最终状态独立通过阶段顺序、
安全区、图框/标题栏、正面积碰撞、跨无关视图尺寸线、投影父子/对齐、三类最小间距和文字/箭头可读性门禁后，
才交给 G2 原子 PlanStore。相同 handoff 和意图产生字节一致的操作序列；工程合法计划仍由独立能力清单决定
`supported` 或 `capability_blocked`。详细边界见 `docs/G3_DETERMINISTIC_LAYOUT_ENGINE.md`。
- [x] G4：实现 C# 最终排版事务。
  - [x] 原生移动尺寸、注释、引线和视图，保持投影关系，并执行计划允许的比例/图幅调整。
  - [x] 采用有界“应用 → 重建 → 真实边界回读 → 碰撞检查 → 有限调整”流程；超限稳定失败。
  - [x] 保存、关闭、只读重开后提交新的最终图纸和验证侧车，不覆盖尺寸阶段图纸。
G4 已加入 SHA-256 锁定的独立 C# DrawingLayoutPlan 合同、八操作编译器、递归上游制品预检和
私有 validate/execute 入口；不会进入默认 semantic MCP。能力门禁同时要求计划使用的原生操作、
六项安全回读和全部必需 G0 边界具有绑定的 live 支持证据，`capability_blocked` handoff 仍可通过
工程完整性检查，但生产创建在 COM 前稳定拒绝。原生候选按冻结六阶段顺序移动尺寸、注释、单一
原生引线和授权视图，并执行授权局部比例、主比例与精确审批图幅；尺寸层级由同阶段冻结尺寸位置
实现。每次只允许最多三轮“应用 → 重建 → 真实边界/语义回读 → 安全区/标题栏/碰撞/投影复核 →
重放同一冻结目标”，不生成计划外坐标，超限稳定失败。事务从干净会话用 `CopyDocument` 创建随机
临时副本，重算 DrawingLayoutPlan、G1、DimensionPlan、尺寸图/侧车及 DimensionPlan 五项嵌套上游
制品哈希；内存核验后保存、关闭、只读重开并要求规范化布局指纹一致，最后双文件原子提交新图纸和
严格验证侧车，失败清理临时/部分输出且不覆盖尺寸阶段图纸。G7 已完成全部实机验收并晋级生产能力，
详细合同见 `docs/G4_DRAWING_LAYOUT_TRANSACTION.md`。
- [x] G5：实现最终布局独立核验器。
  - [x] 核对尺寸值/数量/附着实体和视图语义不变，无悬空对象、越界、新增正面积碰撞或保留区侵占。
  - [x] 规范化布局指纹在保存后内存、事务重开和独立验证之间一致；截图/PDF 仅作附加视觉 QA。
G5 已加入严格、SHA-256 锁定的 G4 验证侧车预检和私有
`verify_committed_part_drawing_layout_plan`。预检不信任事务成功标记：它重新校验最终图纸/侧车的
计划、源图、输出和递归冻结输入绑定，要求 1–3 轮证据连续且末轮通过，并要求 G4 内存/只读重开
阶段的尺寸语义、视图语义和布局指纹完全一致。独立核验器从磁盘重新解析、编译并预检 DimensionPlan，
只把明确授权的 `move_dimension` 位置叠加到期望中，再用原尺寸侧车 stable handle 回读尺寸数量、类型、
目标视图、值、文字、孔变量、公差和模型持久引用。第二条只读链重新捕获真实边界，核验视图配置/
显示状态/父子关系/剖切定义、投影轴、比例/图幅、完整 G1 对象与视图身份、leader owner、安全区、
标题栏和正面积碰撞；独立布局指纹必须等于 G4 两阶段指纹。核验不调用保存、不递增状态，关闭后重算
输出哈希并重跑全部 COM-free 预检以拒绝竞态。详细合同见
`docs/G5_INDEPENDENT_LAYOUT_VERIFICATION.md`。G7 九个正向案例均完成独立只读核验，截图/PDF 不替代
结构化证据。
- [x] G6：新增 `solidworks-finalize-drawing-layout` Skill 和五项布局语义工具。
  - [x] Skill 必须绑定尺寸阶段返回的不可变 request，不能跳回视图基线或绕过尺寸核验。
G6 把完整且 unchanged 的 `DimensionPlanningRequest` 嵌入 `LayoutPlanningRequest`；初始化阶段先重校验
该请求与发布的 `dimension_plan.json`，G3/G6 每次再核对尺寸 handoff 哈希和计划发布目录。第五 Skill
只允许调用状态与五项布局语义工具，不能调用 G4/G5 私有执行动词。发布、校验、创建和独立核验复用
同一请求与同一 `drawing_layout_plan.json`，详细合同见 `docs/G6_FINAL_LAYOUT_SEMANTIC_CHAIN.md`。
- [x] G7：完成布局离线、合同和真实 SolidWorks 矩阵。
  - [x] 覆盖少尺寸、多视图、剖视、详图、辅助视图、孔阵列、高密度尺寸、缩放和授权/未授权换图幅。
G7 的三项不可变证据 Schema、九正一负场景语义门禁、两项矩阵绑定资格语义工具、C# 私有资格创建/
独立核验路由、COM-free 聚合/晋级候选和实机运行器均已实现并通过。最终矩阵在 revision `33.5.0`
覆盖八类操作和六类安全读回；九个正向场景全部保存重开并独立核验，一个未授权换图幅反例稳定拒绝。
汇总 SHA-256 为 `91e95b5c34ad92ac422839d6eb5585983336117bae7dbfc113f8e68be1122ecc`；生产
`plan-current.json` 已与晋级候选逐字节核对并升级为 `1.0.0`，详见
`docs/G7_DRAWING_LAYOUT_LIVE_MATRIX.md`。

### H. 五 Skill 完整生产链发布候选

- [ ] H0：完成用户入口到 COM 再独立回读的五 Skill 发布候选。
  - [x] 建立 H0 COM-free 发布就绪门禁：锁定五 Skill 顺序/allow-list、24 工具/零 prompt 实际发现、
    三份计划 Schema、四份能力清单、G0/G7 哈希绑定和干净 Git commit，并发布严格的一次性就绪报告。
  - [x] 建立 H1 COM-free 完整链证据账本：固定五阶段及全局调用顺序，锁定生产 publish/validate/create/
    verify 工具，拒绝 F7/G7 qualification 替代，并核对请求、计划、handoff、空白图纸与三代后继图纸、侧车、runtime、
    源模型及模板的文件/规范 SHA-256 连续性和一次性证据发布。
  - [x] 建立 H2 COM-free 生产会话预检：冻结 H0、commit、runtime、源模型、模板和唯一新 session root，生成
    固定 16 步生产语义调度及全部 handoff/计划/图纸/侧车/响应/阶段证据路径；blocked 时只发布诊断报告，
    不创建 session root、不启动 SolidWorks。
  - [x] 建立 H3 追加式会话捕获：仅从 ready H2 创建一次性 session manifest，严格按 16 步顺序保存原始
    语义响应；失败响应永久终止该 session，前一阶段未冻结不得进入下一阶段；五阶段完成后重哈希全部制品、
    通过独立 H1 验证并一次性发布 H1 candidate。H3 自身不调用 MCP、HTTP、COM 或 UI 自动化。
  - [x] 建立 H4 单步生产语义代理：每次只接受一个哈希绑定请求，在启动 MCP 前重验 H3 当前唯一下一步，
    仅经 `adapters/codex/server.py` 调用一个公开生产工具并立即追加捕获；锁定 24 工具/零 prompt，禁止
    qualification、私有 executor、HTTP 和直接 COM。调用后超时或非 JSON 等不确定结果永久停止 session，
    防止重放可能已生效的图纸修改。
  - [ ] 五个 Skill 的输入输出和 SHA-256 连续可追踪；每阶段只产生一个计划和一个新的后继图纸。
  - [ ] ViewPlan、DimensionPlan、DrawingLayoutPlan 分别通过独立发布、确定性校验、能力门禁和 C# 合同。
  - [ ] 全程仅调用工程语义 MCP，私有 executor 动词和 COM 不暴露给 Agent。
  - [ ] 最终图纸保存、关闭、只读重开及独立核验通过；源模型、模板和所有上游冻结制品保持不变。
  - [ ] 冻结最终 commit、五个 Skill、三个 Schema/计划、能力清单、runtime、工程图和侧车哈希，并同步文档。

H0 就绪门禁已实现于 `release_candidate/h0_readiness.py`，报告合同为
`release_candidate/contracts/h0-readiness.schema.json`，命令入口为
`scripts/check_h0_release_readiness.py`。门禁不启动 SolidWorks，也不接受 F7/G7 资格事务替代生产事务。
当前报告按预期返回 `blocked`：尺寸能力清单仍为 `0.3.0`，18 类 DimensionPlan 和 F7 六项生产执行元素
仍为 `planned`；因此生产 `create_dimensioned_part_drawing` 必须继续失败关闭。完成 F7 全覆盖实证并正式
晋级 `dimension_planner/capabilities/current.json` 后，方可运行 H0 五 Skill 实机链和冻结 M6 证据。详细
边界与复现方式见 `docs/H0_FIVE_SKILL_RELEASE_CANDIDATE.md`。

H1 证据合同为 `release_candidate/contracts/h1-chain-evidence.schema.json`，验证器与一次性发布器位于
`release_candidate/h1_chain_evidence.py`，命令入口为 `scripts/validate_h1_five_skill_chain.py`。它只验收
真实生产调用保存的响应和制品，不启动 SolidWorks；当前 H0 `blocked` 报告无法作为 H1 输入，因此不会把
尚不存在的 F7/H 实机证据包装为完成状态。完整字段与连续性规则见
`docs/H1_FIVE_SKILL_CHAIN_EVIDENCE.md`。

H2 请求/报告合同、确定性路径表和一次性预检发布器位于
`release_candidate/contracts/h2-session-request.schema.json`、
`release_candidate/contracts/h2-session-preflight.schema.json` 和
`release_candidate/h2_session_preflight.py`，命令入口为
`scripts/prepare_h2_five_skill_session.py`。H2 不接受已有目录或 `validation/` 路径，并再次核对当前 commit/
worktree 与 H0 报告；当前 F7 未晋级状态只能生成 `blocked` 报告。详见
`docs/H2_FIVE_SKILL_SESSION_PREFLIGHT.md`。

H3 会话/阶段合同和追加式捕获器位于
`release_candidate/contracts/h3-session-manifest.schema.json`、
`release_candidate/contracts/h3-stage-capture.schema.json` 与
`release_candidate/h3_session_capture.py`，统一命令入口为
`scripts/h3_five_skill_session.py`。它重新计算 H2 的 16 步调度和确定性输出命名空间，拒绝被修改的 ready
报告；完整使用方式见 `docs/H3_FIVE_SKILL_SESSION_CAPTURE.md`。

H4 单步请求/排他调用声明合同、H3 只读状态检查和 stdio 语义代理位于
`release_candidate/contracts/h4-semantic-step-request.schema.json`、
`release_candidate/contracts/h4-semantic-call-claim.schema.json`、
`release_candidate/h3_session_capture.py` 与 `release_candidate/h4_semantic_step.py`，命令入口为
`scripts/run_h4_five_skill_step.py`。H4 不替 Skill 生成任何计划；Skill/用户必须提供当前步骤的完整参数，
并在阶段边界调用 H3 冻结制品。完整边界见 `docs/H4_FIVE_SKILL_SEMANTIC_STEP.md`。

### 开发顺序、关键路径和里程碑

必须按 `E → F → G → H` 进入发布门禁。G0 的只读 API 实证可在 F4/F5 期间并行，但 G1 及之后必须消费
F 已执行并核验的真实尺寸图纸，不得基于模拟注释或仅依据 ViewPlan 尺寸带提前验收。

单主开发者、固定 SolidWorks 2025 SP5 实机环境下的初步估算：E1-E4 为 1-2 周，F 为 9-12 周，G 为
8-10 周，H 为 1-2 周；考虑 G0 与 F 后半段并行后的总关键路径约 18-25 周。F0/G0 原生 API 实证完成后
必须根据实际可回读能力重新校准排期，不得以进度压力将不可靠能力标记为 `supported`。

里程碑：

1. M1：当前三 Skill 用户入口发布候选通过（E4）。
2. M2：DimensionPlan Schema、尺寸 handoff 和确定性门禁冻结（F3）。
3. M3：尺寸完整实机矩阵通过，第四 Skill 可用（F7）。
4. M4：真实注释边界和确定性布局求解器完成（G3）。
5. M5：高密度最终排版矩阵通过，第五 Skill 可用（G7）。
6. M6：五 Skill 完整生产链发布候选通过（H0）。

## 9. 每次提交的完成定义

- 默认 MCP 工具契约测试通过；
- 当前涉及的 View/Dimension/Layout Planner 单元和 JSON Schema 测试通过；
- C# ViewPlan 合同测试 `45/45` 不回归；F/G 新增各自独立合同套件，禁止用修改既有 45 项断言代替增量覆盖；
- 三 Skill 生产链完成前，三个现有 `SKILL.md` 合同和 Codex/MCP/Schema 三方工具面漂移测试通过；
- F/G 开始后，相应 Skill、handoff、计划、能力清单和语义工具合同测试通过；
- Python `compileall` 和 `git diff --check` 通过；
- C# 改动完成 x64 构建；
- COM 行为改动必须记录 SolidWorks 版本及真实验证结果；
- 创建事务只写全新输出；上游计划、图纸、侧车、模型、模板和 `validation/` 哈希不变；
- 实机验收必须包含保存、关闭、只读重开及独立验证，不以截图或人工观察替代结构化核验；
- 文档中的阶段勾选和能力清单与代码同步。

## 10. 当前迁移说明

上一版 `drawing_planner/executor_bridge.py` 及其两个外部执行器动词已在 D3 删除。生产链不再发现或调用
外部 `$solidworks-plan-drawing-views` Skill、`Invoke-ViewPlanCli.ps1` 或 PowerShell 执行桥。当前生产链会由
Codex 正常发现仓库自有 `.codex/skills/`，但这些 Skill 只能编排默认 semantic MCP，不能成为第二执行层。

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

E0 之后生产入口发生了架构演进：默认 Codex MCP 入口现在经 `adapters/codex/server.py` 委托共享 semantic
MCP，默认工具面扩展为十项，并由仓库自有 `bootstrap-solidworks-host`、
`solidworks-initialize-drawing-handoff` 和 `solidworks-create-drawing-views` 三个 Skill 在上层显式编排。
显式视图 Skill 读取仓库锁定的 `native-v4`、ViewPlan Schema、完整 handoff 和参考路由，只生成一个候选并
提交 `publish_validated_part_drawing_view_plan`；MCP 仍拥有全部确定性门禁、能力评估、原子发布和 C# 事务。
仓库自有主机预检也已迁入 `inspect_solidworks_host`/`bootstrap_solidworks_host` semantic tools，经受控
`/host/bootstrap` 入口运行仓库构建的 x64 helper，不再依赖用户目录中的旧 bootstrap Skill 二进制。

因此 E0 的 45/45 C# 合同、13/13 ViewPlan 实机矩阵和 DrawingPlan 兼容 smoke 作为底层历史基线继续有效，
但其“六工具”和旧主机预检证据不能单独代表当前三 Skill 用户链路已经发布。E1-E4 将重新冻结真实用户
入口、Skill/MCP 边界和仓库自有 HostBootstrap 证据；F/G 随后分别交付尺寸标注和基于真实注释边界的
最终工程图排版，H 再对五 Skill 全链执行最终发布候选收口。
