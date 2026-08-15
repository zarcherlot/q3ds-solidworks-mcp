# G6 最终排版语义链

G6 将仓库自有第五个 Skill `solidworks-finalize-drawing-layout` 接入默认工程语义 MCP。默认工具面现为
24 项：五项生产布局工具加上两项仅用于 G7 矩阵绑定的资格工具；G4/G5 的三个 executor 动词仍然只存在
于 C# 私有执行边界。

## 不可变连续性

`initialize_part_drawing_layout_handoff` 现在必须接收尺寸阶段返回的完整 `DimensionPlanningRequest`。
Python 在进入 COM 前重新验证该请求和 `dimension_plan.json`，并要求传入计划就是该请求发布目录下的
唯一冻结计划。C# initializer 继续独立核验 DimensionPlan、最终尺寸图纸、尺寸侧车和 G0 能力实证。

initializer 返回 `planning_request_context`。第五 Skill 只能在该上下文上补充请求/计划 ID、时间、明确
授权、布局意图和有证据的假设；`source_dimension_request`、layout handoff 绑定和发布目录不得改变。
完整 `LayoutPlanningRequest` 的规范哈希贯穿发布、校验、创建和独立核验，响应同时返回源尺寸请求哈希。

## 五项布局工具

1. `initialize_part_drawing_layout_handoff`：冻结已执行并独立核验的尺寸阶段制品与精确布局边界。
2. `publish_validated_part_drawing_layout_plan`：由仓库确定性 solver 生成唯一候选，通过 G3 门禁后无覆盖
   原子发布 `drawing_layout_plan.json`；`capability_blocked` 仍可发布。
3. `validate_part_drawing_layout_plan`：从 unchanged 请求重新求解，要求结构化计划与唯一候选、磁盘发布物
   完全相同，再调用 C# COM-free parser/compiler/递归输入与能力预检。
4. `create_final_part_drawing`：仅在所有必需能力具有 live-supported 证据时调用 G4 私有原子事务；输出
   `.SLDDRW` 和 `<output>.layout-verification.json` 必须都是新路径。
5. `verify_final_part_drawing`：重新运行所有 Python 门禁和发布绑定，再调用 G5 私有只读独立核验器。

## Prompt 与 Skill 边界

布局意图提示词位于不可变版本包 `drawing_layout_planner/prompt_packs/native-v1/`，manifest 锁定 system/task
字节哈希。提示词只允许产出一个 `LayoutPlanningRequest`；最终坐标、操作相位、能力判断和计划发布由
仓库 solver/validator 决定。

第五 Skill 的 allow-list 为状态工具和上述五项布局工具。它禁止 raw HTTP、第二 MCP 客户端、Python COM、
UI 自动化、legacy DrawingPlan，以及直接调用 `validate_frozen_part_drawing_layout_plan`、
`execute_part_drawing_layout_plan` 或 `verify_committed_part_drawing_layout_plan`。

## 发布状态

G6 合同和实现已完成。G7 完整真实 SolidWorks 矩阵已生成并锁定 live readback 证据，生产 layout
capability manifest 已晋级为 `1.0.0` / `supported`。合法且完全授权的计划现在可通过
`create_final_part_drawing` 和 `verify_final_part_drawing`；若工程门禁或能力绑定不满足，计划仍可按合同发布
为 `capability_blocked`，创建/核验继续显式拒绝。
