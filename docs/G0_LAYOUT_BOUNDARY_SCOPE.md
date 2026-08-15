# G0 SolidWorks 2025 SP5 最终布局边界实证范围

状态：已完成；固定六类矩阵、特殊 ViewPlan 正向样本、隔离标题栏夹具、最终资格聚合和生产能力目录晋级均已通过

目标环境：SolidWorks 2025 SP5，revision `33.5.0`

目标研究协议：`solidworks-layout-boundary-probe` 1.0；后续生产协议：
`solidworks-drawing-layout-plan` 1.0

## 1. 边界和安全边界

G0 只验证重建后真实对象边界和漂移，不创建 DrawingLayoutPlan，不移动任何对象，也不注册 Agent 可见
工具。私有 `POST /api/research/layout-boundary-probe` 接收哈希绑定的已核验尺寸图、已核验 ViewPlan 图，
以及仅用于标题栏正向资格的隔离 G0 夹具；它以只读方式打开图纸，依次发布重建前、重建后、完全关闭后
只读重开的结构化快照，最后发布证据报告。探测过程不保存图纸，全部上游制品必须保持哈希不变。

能力目录固定为：

- 视图轮廓、尺寸显示、普通注释文字、引线和视图标签边界；
- 剖切符号、中心元素、图框和标题栏边界；
- 重建漂移和保存重开漂移。

缺少某类对象的图纸只能让该能力保持 `planned`，不能证明 `unsupported`。需要近似的原生数据同样保持
`planned`，直到多案例实机误差证据证明其确定性误差预算；不得使用截图、像素或人工观察替代结构化读回。

## 2. 当前原生读回策略

精确来源包括 `IView.GetOutline`、`INote.GetExtent`、`IAnnotation.GetLeaderPointsAtIndex`、
`ISheet.GetSize` 和 `ITitleBlock.GetExtents`。尺寸与中心元素通过 `IAnnotation.GetDisplayData` 读取线、箭头和
文字显示数据；`GetLineAtIndex3` 的前四项样式元数据不会作为坐标，箭头按 tip/direction/width/height 解析。
若 `GetTextInBoxWidthAtIndex` 无法提供宽度，报告会明确标记为近似并阻止能力晋级。`INote.GetExtent` 严格按
lower-left XYZ 与 upper-right XYZ 两个角点解析。剖切符号分别按 `IDrSection` 的 line 二维点对、arrow XYZ
点组和 text XYZ 原点处理；文字边界仍需近似，所以保持 `planned`。

每个对象使用稳定类别、所属视图和仓库生成 ID 比较四边界坐标。任一对象集合在重建或重开后无法一一匹配，
或最大坐标漂移超过请求的 `error_budget_m`，相关能力都会稳定停留在 `planned`。

## 3. 后续实机门禁

2026-08-14 已完成首个只读实机候选。输入为 F7 `matrix-live-r14` 的 bracket DimensionPlan、尺寸图纸和
独立核验侧车；三项输入前后哈希一致。图纸包含 3 个模型视图和 1 个尺寸显示对象，共 5 个可比较边界对象。
视图轮廓、图框、重建漂移和保存重开漂移在三阶段的最大坐标漂移均为 `0 m`；尺寸显示边界虽然同样零漂移，
但文字/箭头边界仍包含未校准近似，因此保持 `planned`。该图纸未包含 note、引线、视图标签、剖切符号、
中心元素和原生标题栏对象，这些能力也保持 `planned`。报告总体为 `incomplete` 且无证据门禁 blocker；
最终加固合同重跑的 canonical SHA-256 为
`d32563bcd079801602231a6601ac6ea6db2b1d05ab7d3ddc65350084183392d0`，位于
`C:\Users\admin\Downloads\solidwokrs-mcp-test-g0-20260814\bracket-build2-final\layout-boundary-evidence.json`。
该单案例不会自动或人工改写能力清单。

随后完成的固定六类矩阵从同一 F7 `matrix-live-r14` 的 plate、bracket、threaded、shaft_sleeve、flange、
slot_cavity 冻结制品生成。最终 build3 请求 canonical SHA-256 为
`45a51c53766a72ab6ff09846fe97ea82e4f30606eb379d03dc16100775528997`；汇总文件 SHA-256 为
`41027d079f992fb26a044fe670a39fef6bb865145ceda64b667a4f349e587d32`，位于
`C:\Users\admin\Downloads\solidwokrs-mcp-test-g0-20260814\six-category-build3\layout-g0-matrix-summary.json`。
六类全部覆盖视图轮廓、图框、重建漂移和只读重开漂移，最大漂移为 `0.000174150161 m`，低于
`0.0005 m` 预算；threaded、shaft_sleeve、flange 覆盖 note 和引线。三条 note 均原生读回为带一条引线的
`M8/M16 螺纹孔`，并非仓库管理的辅助标签。六类尺寸显示边界均可观测且零漂移，但仍含文字宽度近似，故
保持 `partial`。矩阵无 blocker，上游哈希不变，能力清单未被改写。

最终补充样本均来自生产 ViewPlan 1.4 事务并通过独立只读核验：辅助标签和中心元素证据位于
`special-view-build3`，全剖切割符号位于 `special-section-build1`。GB A3 模板本身没有活动 `ITitleBlock`
对象，因此隔离 C# 夹具从已核验 ViewPlan 图纸复制新文件，使用 sheet-format note 创建唯一标题栏，并在保存
重开前后通过 `ITitleBlock.GetExtents` 读回相同边界；该夹具不进入 semantic MCP，也不修改生产图纸。

最终 `solidworks-layout-g0-qualification` 报告绑定六类矩阵及三份补充证据，SHA-256 为
`292825190b481261bb37f9e6b6154d01be2917e7887a2745ddc1aae7e42f79a4`。九项精确能力标记为
`supported`；尺寸显示与剖切符号的线、箭头和文字锚点在全部重建/重开比较中稳定且在预算内，但原生 API
没有精确 glyph extent，因此显式标记为 `unsupported`，不会将近似值用于 G1 碰撞安全边界。能力目录已晋级
到 `registry_version=1.0.0`、`verification=live_complete`，G0 门禁完成。

## 5. G7 前精确补充资格

G7 前又完成一轮精确结构化补充读回：尺寸显示边界由 `IAnnotation.GetDisplayData` 的线段、原生箭头数据、
文字度量和引用位置共同构成，剖切符号由原生 section line/text 结构绑定；两类结果均在重建和只读重开中
零超预算漂移。最终资格 ID 为 `G0-EXACT-COMPLETE-20260815`，资格文件 SHA-256 为
`caa397f008c71391c70fb5c87c200a21e00721b59beb005b890a03acb5437961`。当前能力目录已晋级为
`registry_version=1.1.0`，十一项边界能力全部为 `supported`，目录 SHA-256 为
`3afee02a2969619144fe7506aa636772e178ce1205afb06f511f79035475512f`。前述 `1.0.0` 九支持/两不支持
状态保留为历史资格结论，不再代表当前生产目录。
