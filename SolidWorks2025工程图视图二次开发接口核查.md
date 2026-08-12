# SolidWorks 2025 工程图视图二次开发接口核查

## 1. 核查结论

本报告基于本机 SolidWorks 2025 SP5.0（内部版本 33.5.0）的实际 COM/Interop 程序集及官方 2025 API 帮助核查。

- 大多数工程图“绘制动作”可以封装。
- 剖视、详图、辅助视图、局部剖和中心要素可以实现，但底层依赖活动文档、活动视图或选择集，必须通过状态隔离适配器封装。
- 视图语义判断、最优视图选择、任意三维方向的直接设置、任意模板区域的可靠语义识别以及视觉清晰度判断，无法由 SolidWorks 原生 API 独立可靠完成。

## 2. 绘制要素与接口能力

| 绘制要素 | 结论 | SolidWorks 2025 主要接口 | 限制 |
|---|---|---|---|
| 基本视图 | 可直接封装 | `IDrawingDoc.CreateDrawViewFromModelView3` | 模型视图名称必须精确匹配；创建时可能受自动缩放系统选项影响，之后必须重新设置比例并读回 |
| 投影视图 | 可条件封装 | `CreateUnfoldedViewAt3` | 必须预先选择母视图，接口本身不接收母视图参数 |
| 全剖、半剖、偏移剖、对齐剖、移出断面 | 可条件封装 | `CreateSectionViewAt5`、`swCreateSectionViewAtOptions_e` | 必须先在母视图中创建并选择剖切线，不能做成真正无状态的底层调用 |
| 局部剖 | 可条件封装 | `CreateBreakOutSection` | 只有深度参数，边界草图、活动视图和选择上下文必须提前准备 |
| 详图 | 可条件封装 | `CreateDetailViewAt4` | 母视图和详图轮廓主要通过当前选择及草图上下文传递 |
| 辅助视图、斜视图 | 可条件封装 | `CreateAuxiliaryViewAt2` | 必须选择母视图中的唯一参考直边 |
| 等轴测、标准方向 | 可直接封装 | `CreateDrawViewFromModelView3` 加标准或命名模型视图 | 不应硬编码本地化视图名称，应先解析模型实际视图名 |
| 任意三维观察方向 | 无直接接口；可条件绕行 | `IModelView.Orientation3`、临时命名视图、`CreateDrawViewFromModelView3` | `IView.ModelToViewTransform` 官方明确为只读，setter 未实现；绕行需要临时修改模型内存状态 |
| 配置与显示状态 | 可直接封装 | `IView.ReferencedConfiguration`、`DisplayState`、`LinkParentConfiguration` | 应在创建后显式设置并重建确认 |
| 视图比例 | 可直接封装 | `ScaleDecimal`、`ScaleRatio`、`UseSheetScale`、`UseParentScale` | 基本视图创建时可能被自动缩放覆盖，必须再次断言 |
| 视图位置 | 可直接封装 | `IView.Position`、`SetXform` | 对齐视图只能沿约束方向移动；`SetXform`只设置 X、Y 和比例，不设置三维方向 |
| 投影对齐 | 可直接封装 | `AlignWithView`、`AlignHorizontalTo`、`AlignVerticalTo`、`GetAlignment` | 调整后要验证父子关系和实际位置 |
| 第一角、第三角投影 | 可直接封装 | `ISheet.GetProperties2`、`SetProperties2` | 修改纸张属性可能影响已有视图布局，应在建图前冻结 |
| 显示模式 | 可直接封装 | `SetDisplayMode3`、`GetDisplayMode2` | 支持线框、隐藏线、着色、着色并显示边线等 |
| 切线边、隐藏边 | 可直接或条件封装 | `SetDisplayTangentEdges2`、`HiddenEdges` | 单边控制需要解析并选择具体投影边 |
| 线色、线型、线宽、图层 | 可条件封装 | `SetLineColor`、`SetLineStyle`、`SetLineWidth`、`CreateLayer2` | 命令作用于当前选择或当前图层，必须隔离选择状态 |
| 中心标记 | 可条件封装 | `InsertCenterMark3`、`AutoInsertCenterMarks2` | 精确插入需要选择圆边；自动插入难以保证复合孔不重复 |
| 对称中心线、轴线 | 可条件封装 | `InsertCenterLine2` | 必须先选择对应实体；API不会自动判断哪一对边代表对称关系 |
| 剖视标签 | 可封装 | `IDrSection.SetLabel2` | 标签重复会受当前制图标准约束 |
| 详图标签 | 可封装 | `IDetailCircle.SetLabel`、`SetLabelPosition` | 需先取得对应详图轮廓对象 |
| 投影箭头标签 | 可封装 | `IProjectionArrow.SetLabel` | 箭头对象必须能从母视图稳定解析 |
| 视图名称 | 可直接封装 | `IView.SetName2` | 不应把名称作为唯一业务 ID，用户可重命名且存在本地化问题 |
| 图框和纸张范围 | 可读取并封装 | `ISheet.GetTemplateSketch`、`GetSize`、`GetZoneMargin` | 真正内图框应从模板草图解析，不能只采用纸张尺寸 |
| 标题栏、技术要求区范围 | 部分可实现 | `INote.GetExtent`、模板草图几何 | API能给出注释范围，但不能可靠判断任意注释属于标题栏还是技术要求 |
| 布局和防重叠 | 可由上层算法实现 | `IView.GetOutline`、`Position` | SolidWorks没有满足尺寸带约束的原生布局求解器 |
| 保存、重建和读回校验 | 可直接封装 | `EditRebuild3`、`ForceRebuild3`、`Save3`及各类 Getter | 高可靠流程应保存、关闭、只读重开后再次验证 |

## 3. 推荐的解耦调用接口

业务层不应直接接触 `IView`、COM 对象、当前选择集或本地化名称。建议对外暴露纯数据调用：

```csharp
Result<ViewHandle> CreateBaseView(BaseViewSpec spec);
Result<ViewHandle> CreateProjectedView(ProjectedViewSpec spec);
Result<ViewHandle> CreateSectionView(SectionViewSpec spec);
Result<ViewHandle> CreateDetailView(DetailViewSpec spec);
Result<ViewHandle> CreateAuxiliaryView(AuxiliaryViewSpec spec);
Result<ViewHandle> CreateBrokenOutSection(BrokenOutSpec spec);

Result ApplyViewStyle(ViewHandle view, ViewStyleSpec spec);
Result PlaceView(ViewHandle view, PlacementSpec spec);
Result AddCenterGeometry(ViewHandle view, CenterGeometrySpec spec);

Result<ViewSnapshot> RebuildAndVerify(ViewHandle view);
```

### 3.1 数据和对象边界

- `ViewSpec`只保存模型路径、配置、方向定义、比例、位置、显示方式等纯数据。
- `ViewHandle`使用业务 ID 映射实际视图，不直接把 COM 对象传出适配层。
- 几何实体使用持久引用、模型拓扑 ID 或明确的坐标签名，不使用“当前选中的第一个对象”。
- SolidWorks 2025 专有逻辑放在 `SolidWorks2025Adapter` 中，业务层不依赖具体版本。

### 3.2 选择状态隔离

所有依赖选择的操作统一经过 `SelectionScope`：

```text
保存当前文档、图纸和选择状态
→ 激活目标图纸及母视图
→ 清空选择
→ 解析并唯一选择冻结实体
→ 调用 SolidWorks API
→ 清空选择并恢复原状态
→ 重建和读回验证
```

### 3.3 任意方向事务

任意三维方向绕行应封装到独立的 `TemporaryNamedViewTransaction`：

1. 保存模型当前活动视图和状态；
2. 设置临时模型视图方向；
3. 创建唯一临时命名视图；
4. 创建对应工程图视图；
5. 删除临时命名视图；
6. 恢复模型状态且不保存模型改动。

该方法仍然依赖模型的内存状态，因此不能视为完全无副作用的原生调用。

## 4. 原生 API 目前不能可靠完成的部分

### 4.1 自动理解视图目的

`purpose`、制造用途、检验用途及不可替代性没有对应的 SolidWorks 接口，只能保存在外部规划数据中。

### 4.2 自动选择最小充分视图集

API可以创建视图，但不会判断还缺哪个视图、哪个视图冗余。需要上层 B-Rep 分析和工程制图规则引擎。

### 4.3 直接设置任意三维视图方向

`IView.ModelToViewTransform`虽然在 Interop 中显示 setter，但官方 2025 文档明确说明 setter 未实现。`IView.Angle`只能旋转纸面中的视图，不能改变三维观察方向。

### 4.4 无选择状态地创建派生视图

投影、剖视、详图、辅助视图、局部剖和中心线都依赖隐式活动文档或选择集。适配器可以隐藏这种耦合，但不能消除 COM 底层的状态依赖。

### 4.5 对任意模板可靠识别标题栏和技术区域

可以读取模板草图和注释包围框；若模板没有统一图层、属性或命名约定，API无法保证语义识别唯一。

### 4.6 自动判断视觉清晰度

API可以读取显示方式和投影几何，但不能可靠判断线条是否拥挤、着色是否模糊或工程师是否容易读懂。只能采用规则指标并结合最终视觉复核。

### 4.7 原生完成带尺寸预留的最优布局

可以自行编写布局算法，但 SolidWorks 没有直接提供满足图框、禁入区和尺寸带净距要求的自动布局接口。

## 5. 实施建议

- 将规划层、执行层和验证层分开，执行器不得临时补选视图或猜测实体。
- 所有选择依赖操作必须具备唯一实体解析、选择清理和状态恢复。
- 创建后显式设置配置、显示状态、比例、位置和显示模式，不依赖文档默认值。
- 所有视图保存后关闭并只读重开，验证类型、母视图、配置、比例、位置、标签和中心要素。
- 派生视图的剖切路径、详图轮廓、局部剖边界和辅助参考边应作为冻结数据输入。
- 无法唯一解析参考实体时返回阻塞错误，不自动选择“最相近”的实体。

## 6. 官方接口参考

- [CreateDrawViewFromModelView3](https://help.solidworks.com/2025/english/api/sldworksapi/SOLIDWORKS.Interop.sldworks~SOLIDWORKS.Interop.sldworks.IDrawingDoc~CreateDrawViewFromModelView3.html)
- [CreateSectionViewAt5](https://help.solidworks.com/2025/english/api/sldworksapi/SOLIDWORKS.Interop.sldworks~SOLIDWORKS.Interop.sldworks.IDrawingDoc~CreateSectionViewAt5.html)
- [CreateAuxiliaryViewAt2](https://help.solidworks.com/2025/english/api/sldworksapi/SOLIDWORKS.Interop.sldworks~SOLIDWORKS.Interop.sldworks.IDrawingDoc~CreateAuxiliaryViewAt2.html)
- [ModelToViewTransform](https://help.solidworks.com/2025/english/api/sldworksapi/SOLIDWORKS.Interop.sldworks~SOLIDWORKS.Interop.sldworks.IView~ModelToViewTransform.html)

## 7. 本机验证环境

- SolidWorks：2025 SP5.0
- 内部版本：33.5.0
- 架构：x64
- COM启动：通过
- `sldworks.tlb`：存在
- `SolidWorks.Interop.sldworks.dll`：存在
- `SolidWorks.Interop.swconst.dll`：存在
- `SolidWorks.Interop.swpublished.dll`：存在

