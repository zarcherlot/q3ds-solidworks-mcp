# F0 SolidWorks 2025 SP5 尺寸原生 API 实证范围

状态：已完成；仓库原生 live 探测执行链、研究语料覆盖矩阵和生产冻结
ViewPlan/图纸/核验侧车复验均已完成，精确文字边界已实证为 `unsupported`；其余能力按当前门禁
保持 `planned`，不得据此提前标记为 `supported`
目标环境：SolidWorks 2025 SP5，revision `33.5.0`
目标协议：F0 探测协议 `solidworks-dimension-api-probe` 1.0；后续生产协议
`solidworks-dimension-plan` 1.0

## 1. 当前交付边界

F0 只冻结原生 API 能力范围与实证方法，不注册 Agent 可见工具，不创建 DimensionPlan，也不把
legacy `auto_dimension_drawing`、`add_drawing_dimension` 或 `add_hole_callout` 作为生产后端。
这些旧动词只提供 API 调研线索；它们缺少上游不可变绑定、稳定尺寸身份、附着实体持久引用、
实际文字边界、无覆盖事务和保存重开独立核验。

本阶段新增：

- `dimension-api-probe.schema.json`：严格、完整、有序的探测请求；
- `dimension-api-evidence.schema.json`：结构化 live/offline 实证报告；
- `dimension_planner/capabilities/current.json`：固定十四项能力目录；
- Python 确定性证据评估器；
- 独立 C# COM-free 请求合同和独立尺寸合同测试套件。
- C# Execution Service 内部研究入口 `POST /api/research/dimension-probe`；
- 事务副本上的模型尺寸导入、显示尺寸遍历、结构化读回、保存关闭和只读重开探测器；
- `scripts/run_dimension_f0_live_probes.py`：只通过 HTTP 编排 C# 探测并运行 Python 证据门禁。

内部研究入口不注册为 Agent 可见 MCP 工具，不属于生产 `DimensionPlan` 工具面。它只接受完整、
有序、哈希绑定的 F0 请求，在全新发布目录内复制上游制品，并最后发布证据报告。

F0 探测请求支持两种明确分离的来源：

- `research_model_drawing_pair`：只用于 F0 原生 API 实证，绑定同名 `.SLDPRT/.SLDDRW` 研究语料对；
- `frozen_viewplan_drawing`：绑定 ViewPlan、已核验上游图纸和核验侧车，用于生产链一致性复验。

研究语料可以证明 SolidWorks 原生 API 的创建和回读能力，但不能替代 F1 的不可变尺寸 handoff，
也不能直接作为 F4 的生产事务输入。

## 2. 固定能力矩阵

| 能力 | SolidWorks 2025 原生入口/读回 | 初始状态 | 升级条件 |
|---|---|---|---|
| 模型尺寸导入 | `IDrawingDoc.InsertModelAnnotations3` | `planned` | 返回注释逐项可遍历并跨重开一致 |
| 显示尺寸遍历 | `IView.GetFirstDisplayDimension5` / `IDisplayDimension.GetNext5` | `planned` | 无遗漏、无循环、类型和值稳定 |
| 附着实体与持久引用 | `IAnnotation.GetAttachedEntities3`、`GetAttachedEntityTypes`、`IModelDocExtension.GetPersistReference3` / `GetObjectByPersistReference3` | `planned` | 所有必需附着非空且跨会话解析为同一实体 |
| 标注位置 | `IDisplayDimension.GetAnnotation` → `IAnnotation` 位置接口 | `planned` | 图纸坐标可写、可读且跨重开在公差内一致 |
| 文字实际边界 | `IAnnotation.GetDisplayData` → `IDisplayData` 文本/线/箭头显示数据 | `unsupported` | 2025 SP5 只能稳定读回锚点、高度、字体和显示图元，不能恢复精确字形宽度 |
| 线性尺寸 | 原生显示尺寸创建/模型尺寸导入 | `planned` | 见通用升级条件 |
| 直径尺寸 | 原生显示尺寸创建/模型尺寸导入 | `planned` | 直径语义和符号精确读回 |
| 半径尺寸 | 原生显示尺寸创建/模型尺寸导入 | `planned` | 半径语义和符号精确读回 |
| 角度尺寸 | 原生显示尺寸创建/模型尺寸导入 | `planned` | 角度类型、单位和值精确读回 |
| 孔标注 | `IDrawingDoc.AddHoleCallout2` 及 callout 变量读回 | `planned` | 孔径、深度、数量、附着和格式完整读回 |
| 倒角尺寸 | `IDrawingDoc.AddChamferDim` 及显示尺寸读回 | `planned` | 形式、长度/角度和附着完整读回 |
| 公差 | `IDimension.Tolerance` / `IDimensionTolerance` | `planned` | 仅受信输入，类型和值跨重开一致 |
| 前后缀 | `IDisplayDimension` 文本接口 | `planned` | 原生格式、符号和跨重开文本一致 |
| 保存重开稳定身份 | 尺寸身份 + 附着实体持久引用 + 规范化指纹 | `planned` | 保存、关闭、只读重开和独立核验全部一致 |

API 方法和持久引用语义以 SolidWorks 2025 官方 API Help 为准：

- [InsertModelAnnotations3](https://help.solidworks.com/2025/english/api/sldworksapi/solidworks.interop.sldworks~solidworks.interop.sldworks.idrawingdoc~insertmodelannotations3.html)
- [IDisplayDimension.GetAnnotation](https://help.solidworks.com/2025/English/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IDisplayDimension~GetAnnotation.html)
- [IAnnotation.GetAttachedEntityTypes](https://help.solidworks.com/2025/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IAnnotation~GetAttachedEntityTypes.html)
- [IModelDocExtension.GetObjectByPersistReference3](https://help.solidworks.com/2025/English/api/sldworksapi/SOLIDWORKS.Interop.sldworks~SOLIDWORKS.Interop.sldworks.IModelDocExtension~GetObjectByPersistReference3.html)
- [Persistent Reference IDs](https://help.solidworks.com/2025/english/api/sldworksapiprogguide/Overview/Persistent_Reference_IDs.htm)
- [IAnnotation.GetDisplayData](https://help.solidworks.com/2025/english/api/sldworksapi/SOLIDWORKS.Interop.sldworks~SOLIDWORKS.Interop.sldworks.IAnnotation~GetDisplayData.html)
- [IView.GetDimensionInfo7](https://help.solidworks.com/2025/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IView~GetDimensionInfo7.html)
- [IView.GetDimensionDisplayString5](https://help.solidworks.com/2025/English/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IView~GetDimensionDisplayString5.html)

## 3. 通用升级门禁

任一能力只有同时满足以下条件才能从 `planned` 升为 `supported`：

1. 在 revision `33.5.0` 的交互桌面通过仓库原生 C# Execution Service 调用原生 API；
2. 使用已经独立核验的 ViewPlan、上游 `.SLDDRW` 和核验侧车，执行前后哈希完全不变；
3. 只在事务副本中写入，输出使用全新路径；
4. 完成内存读回、保存、关闭、只读重开和独立核验；
5. 尺寸身份、类型、值、目标视图、附着实体、位置和实际文字边界全部结构化读回；
6. 不存在悬空、未计划、重复或部分提交的尺寸；
7. 报告通过 `dimension-api-evidence.schema.json` 和 `evaluate_f0_evidence`。

若 SolidWorks API 忽略请求、无法唯一选择实体、持久引用跨重开漂移或只能获得文字锚点，相关能力
继续保持 `planned` 或按实证结论标记 `unsupported`，并向后续 DimensionPlan 返回
`capability_blocked`。不能以截图、人工观察或 UI 自动化替代结构化核验。

## 4. 实机开始条件

实机探测前必须：

1. 通过仓库 `bootstrap-solidworks-host` Skill 的 no-repair 主机验证；
2. 使用全新的输出目录，且不位于 `validation/`；
3. F0 可输入哈希绑定的同名模型/工程图研究语料对；生产复验必须输入已独立核验的 ViewPlan、图纸和侧车；
4. 运行前后记录源模型、ViewPlan、上游图纸和侧车 SHA-256；
5. 将每类能力至少覆盖一个正向案例和一个稳定失败案例。

2026-08-12 已在 revision `33.5.0` 上通过 no-repair 主机验证，并对用户语料的四组同名
`.SLDPRT/.SLDDRW` 研究对执行事务探测。每组从哈希绑定模板创建四个标准视图，完成模型尺寸导入，
并显式调用半径、直径、孔标注、倒角、公差与前后缀原生 API；无选择的稳定失败探测也逐项确认
相关 API 不会悬空创建标注。四组导入数量分别为 14、13、20、10，共记录 70 个显示尺寸。
所有尺寸的稳定 ID、类型、SI 值、图纸位置、显示字符串和规范化身份合同，在事务模型与图纸均关闭后
跨重开完全一致。47 个带附着实体记录共包含 79 个有效实体引用，79/79 个原始工程图域持久引用均在
只读重开后以状态 0 解析。`GetAttachedEntities3` 另返回 2 个 `type=0/entity=null` 占位槽；探测器将其
记录在 `skipped_slots`，不再错误计作附着实体。输入模型、工程图和模板哈希均未变化。当前汇总制品为
`C:\Users\admin\Downloads\solidwokrs-mcp-test-f0-dev-20260812-continued\live-summary-build17-accepted\dimension-f0-live-probe-summary.json`
（SHA-256 `05b7aa46e801acbd20d0ccf01b5f1279d0988c152b3f0a9433e492be6ea6022a`）。汇总矩阵的
十四项 `research_coverage` 均为 `covered`，没有 partial 能力。

该矩阵同时稳定确认普通显示尺寸的 `IDisplayData` 只提供文字锚点、高度、字体和显示图元，不能恢复
精确字形宽度，因此 `annotation_text_bounds` 判定为 `unsupported`。研究语料已覆盖线性、直径、半径、
角度、孔标注、倒角、非零双边公差和前后缀；研究覆盖已经完成。后续定位到
`VIEW_PLAN_DRAWING_SAVE_FAILED` 的根因是对 initializer `.SLDDRW` 执行字节级 `File.Copy` 后，
SolidWorks 对副本的 `Save3` 即使在尚未创建视图时也返回通用错误 1。ViewPlan 事务现改用 SolidWorks
原生 `ISldWorks.CopyDocument`，并在调用前显式要求没有打开文档；若用户会话中存在文档则返回稳定
阻塞，不会替用户关闭文档。修复后的正式语义事务保持 initializer SHA-256 不变，创建四个模型关联
视图，`Save3(errors=0)`，完成只读重开核验和独立 verify。

2026-08-12 的 build18 最终矩阵包含四个 `research_model_drawing_pair` 和一个
`frozen_viewplan_drawing`，五个案例均为 `evidence_ready`，无失败；冻结案例从已核验 ViewPlan 图纸
导入 18 个模型尺寸并完成保存、关闭、只读重开和结构化证据评估。最终汇总为
`C:\Users\admin\Downloads\solidwokrs-mcp-test-f0-dev-20260812-continued\live-summary-build18-frozen\dimension-f0-live-probe-summary.json`
（SHA-256 `4825740c33e194cac695c13b2f3458ac3c332b833616763cf17ba7ec88bc0ca9`），矩阵为
`research_coverage_complete=true`、`production_frozen_case_count=1`、`overall_status=complete`。
F0 完成表示能力范围和实证结论已冻结，并不把十三项 `planned` 能力提前提升为 `supported`；
`annotation_text_bounds` 继续为 `unsupported`。

当前探测器会记录 `IView.GetFirstDisplayDimension5` / `IDisplayDimension.GetNext5`、
`IView.GetDimensionInfo7` / `GetDimensionDisplayString5` 的稳定工程图值和显示字符串、
`IAnnotation.GetAttachedEntities3`、持久引用、图纸坐标、`IAnnotation.GetDisplayData` 的文字/线/弧/
箭头数据、公差、前后缀和孔变量，并比较内存与只读重开结果。SolidWorks 2025 API 对普通显示尺寸
只提供文字锚点、高度、字体和显示图元，尚未证明可恢复精确字形宽度；因此文字实际边界门禁继续
fail-closed，依赖该通用门禁的能力不得提前升级。
