---
name: solidworks-dimension-drawing
description: 基于已独立核验的 ViewPlan 1.4 单零件工程图和仓库原生 dimension-planning handoff，生成唯一且完整的 DimensionPlan 1.0 候选，按同一不可变请求依次发布、校验、事务创建并独立核验尺寸化 .SLDDRW。适用于模型尺寸、PMI、孔标注、线性/角度/直径/半径/基准/公差和可信用户批准输入；不用于装配体、视图规划、最终排版、旧 DrawingPlan、直接 COM 或私有执行器调用。
---

# SolidWorks Dimension Drawing

## Overview

从已核验的关联工程图开始，只使用仓库冻结证据规划尺寸。生成一个完整候选，并通过五个尺寸语义工具保持 DimensionPlan、DimensionPlanningRequest、输出路径和 SHA-256 连续一致。

## Allowed semantic tools

- `solidworks_status`
- `initialize_part_drawing_dimension_handoff`
- `publish_validated_part_drawing_dimension_plan`
- `validate_part_drawing_dimension_plan`
- `create_dimensioned_part_drawing`
- `verify_dimensioned_part_drawing`

## Hard boundaries

- 只编排上面的工程语义工具。不要调用私有 executor 动词、原始 HTTP、第二个 MCP 客户端、Python COM、UI 自动化或任何旧 DrawingPlan 1.0 工具链。
- 不保存或修改源模型、源工程图、ViewPlan、ViewPlan 验证侧车、dimension handoff、已发布 DimensionPlan 或 `validation/` 中的任何制品。
- 初始化器负责只读提取和发布 handoff；技能只在内存中生成候选。不要自行创建、修补或覆盖 JSON 文件。
- 发布后禁止修改候选、请求或生产者信息；禁止生成第二候选、降级不支持的尺寸或再次发布替代计划。
- 新工程图和 `.dimension-verification.json` 侧车必须使用全新路径。不得覆盖尺寸阶段输入图纸。
- `capability_blocked` 是有效且必须保留的规划结果，但不能进入创建或独立核验。

## Evidence policy

- 开始规划前完整读取 `dimension-planner/prompt_packs/native-v1/manifest.json`、`system.md`、`task.md`，并以 manifest 中的 `producer` 原样填写计划。
- 完整读取初始化结果中的 handoff、`dimension_planner/contracts/dimension-plan.schema.json` 和 `dimension_planner/capabilities/current.json`。仓库 Schema、验证器和能力清单始终优先。
- `model_or_pmi` 只引用 handoff 中真实存在的模型尺寸、PMI 或制造特征 ID。
- `user_confirmed_input` 只引用本次初始化 handoff 中保留的批准输入 ID；不得从普通对话推断批准状态。
- `reference_geometry_measurement` 只能生成 reference 维度，`manufacturing_requirement` 必须为 false，且不得带公差、配合或制造意图。
- 不发明名义值、单位、孔数量、附件、持久引用、前后缀、公差、配合、基准语义或能力支持。证据不足时记录明确的 intentional omission，而不是猜测。
- 每个尺寸必须绑定恰当视图、可信来源、模型持久引用和图纸位置；覆盖制造/检验意图的同时消除重复尺寸和可推导冗余。

## Workflow

1. 调用 `solidworks_status`，确认执行服务可用。不要以启动状态代替后续确定性门禁。
2. 调用 `initialize_part_drawing_dimension_handoff`，传入已发布 `view_plan.json`、已独立核验的 `.SLDDRW`、其 `.verification.json` 侧车和一个全新的尺寸发布目录。只有用户已经明确批准且具有批准人、时间和引用时，才传 `approved_user_inputs`。
3. 要求初始化状态为 `ready`、`handoff_integrity` 为 `pass`。保存返回的完整 `planning_request` 和 `planning_request_sha256`；后续每一步原样复用，不能重新构造。
4. 依据完整 handoff、DimensionPlan Schema、能力清单和版本化 prompt pack，在内存中创建恰好一个完整 DimensionPlan 1.0 候选。`producer` 必须与 pack manifest 完全一致。
5. 调用 `publish_validated_part_drawing_dimension_plan`，传入该候选和原始 request。若 `rejected`，报告确定性问题并停止。若 `published` 且 `capability_blocked`，报告已保留的计划路径、哈希和阻塞能力，然后停止。
6. 对同一候选、同一 request 和预定的新输出路径调用 `validate_part_drawing_dimension_plan`。要求 `VALID`、执行器 `ok` 且 `execution_readiness` 为 `supported`；任何 `EXECUTOR_REJECTED` 或能力阻塞都停止。
7. 调用 `create_dimensioned_part_drawing`，仍传入完全相同的三项输入。只有返回 `COMPLETED`、`ok: true`、已提交新图纸和验证侧车时才继续。
8. 调用 `verify_dimensioned_part_drawing`，使用同一候选、request 和已提交输出路径。要求独立只读重开核验 `COMPLETED` 且 `verified: true`。
9. 汇报 handoff、request、DimensionPlan、输出图纸和两类侧车的路径及 SHA-256，并明确源模型和全部上游冻结制品未变化。

## Stop conditions

遇到 `blocked`、`rejected`、`capability_blocked`、`EXECUTOR_REJECTED`、`FAILED`、SHA-256 不连续、已存在输出、证据歧义或任何来源/附件缺失时立即停止。保留已经原子发布的不可变制品，不修补、不覆盖、不绕过门禁，并向用户报告稳定错误码和缺失证据。
