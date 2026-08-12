# 参考资料导航

## 基础资料

- [SolidWorks 单零件原生自动化工作流](general/solidworks-single-part-workflow.md)：第一步和第四步使用，定义原生自动化边界、数据来源、包围框检查、导出复核和失败处理。
- [零件分类判断方法](general/part-classification.md)：第二步使用，判断十大类别和多类别零件的主次分类。
- [视图选择与图线规则](general/view-selection-and-linework.md)：第二步使用，确定基本视图、剖视、断面、图线、颜色和显示方式。
- [通用尺寸标注规则](general/general-dimensioning-rules.md)：第三步使用，处理尺寸、字体、模型信息边界、测量参考标注、技术要求和材料字段。
- [最终修改与审核检查](general/final-review-checklist.md)：第四步使用，执行模型一致性、视图、尺寸、孔、螺纹、标准结构和文件版本审核。

## 第二步：零件类别与视图资料

先读取分类判断方法，再读取主类别和所有显著次类别的规则。需要确认推荐视图布局时，同时查看对应视觉资料。

| 类别 | Markdown 规则 | 配套视觉资料 |
| --- | --- | --- |
| 轴类 | [shaft-parts.md](shaft-parts/shaft-parts.md) | [shaft-parts.png](shaft-parts/shaft-parts.png) |
| 法兰、盘盖类 | [flange-and-disc-parts.md](flange-and-disc-parts/flange-and-disc-parts.md) | [flange-and-disc-parts.png](flange-and-disc-parts/flange-and-disc-parts.png) |
| 套筒类 | [sleeve-parts.md](sleeve-parts/sleeve-parts.md) | [sleeve-parts.png](sleeve-parts/sleeve-parts.png) |
| 板类 | [plate-parts.md](plate-parts/plate-parts.md) | [plate-parts.png](plate-parts/plate-parts.png) |
| 箱体、壳体类 | [housing-parts.md](housing-parts/housing-parts.md) | [housing-parts.png](housing-parts/housing-parts.png) |
| 支架、座体类 | [bracket-parts.md](bracket-parts/bracket-parts.md) | [bracket-parts.png](bracket-parts/bracket-parts.png) |
| 杆件、叉架、拨动类 | [rod-and-fork-parts.md](rod-and-fork-parts/rod-and-fork-parts.md) | [rod-and-fork-parts.png](rod-and-fork-parts/rod-and-fork-parts.png) |
| 传动与异型类 | [transmission-and-special-parts.md](transmission-and-special-parts/transmission-and-special-parts.md) | [transmission-and-special-parts.png](transmission-and-special-parts/transmission-and-special-parts.png) |
| 紧固与连接件 | [fasteners-and-connectors.md](fasteners-and-connectors/fasteners-and-connectors.md) | [fasteners-and-connectors.jpg](fasteners-and-connectors/fasteners-and-connectors.jpg) |
| 弹性、管路与其他专用件 | [springs-and-special-parts.md](springs-and-special-parts/springs-and-special-parts.md) | [springs-and-special-parts.jpg](springs-and-special-parts/springs-and-special-parts.jpg) |

## 第三步：特征标注资料

建立逐特征清单后，只要模型中存在相应特征，就读取对应规则：

- [实体和总体外形](features/overall-shape.md)
- [孔](features/holes.md)
- [孔组与阵列](features/hole-patterns-and-arrays.md)
- [槽与凹腔](features/slots-and-pockets.md)
- [台阶、轴肩与法兰层级](features/steps-shoulders-and-flanges.md)
- [螺纹、中心孔与滚花](features/threads-center-holes-and-knurling.md)
- [倒角、圆角与过渡圆弧](features/chamfers-fillets-and-transitions.md)
- [斜面、锥面、球面、曲面与偏心结构](features/inclined-conical-curved-and-eccentric-features.md)
- [凸台、轮毂、耳板、支脚与加强筋](features/bosses-hubs-lugs-feet-and-ribs.md)
- [薄壁、壳体与加强结构](features/thin-walls-shells-and-ribs.md)
- [对称、镜像与重复特征](features/symmetry-mirrors-and-repeated-features.md)
- [花键、齿轮与传动特征](features/splines-gears-and-transmission-features.md)
- [钣金](features/sheet-metal.md)
- [铸造类特征](features/castings.md)

## 默认不启用

[暂不默认启用的公差与表面要求规则](general/deferred-tolerancing-rules.md) 原样保留，但默认不作为正式出图依据。只有用户明确要求启用，或任务是审阅该文档本身时才读取。即使启用，任何公差、配合、粗糙度、热处理或表面处理仍必须有受控工程来源。
