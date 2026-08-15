Read the complete immutable handoff, layout planning request schema, layout plan schema, current capability manifests, and original DimensionPlanningRequest. Construct one and only one LayoutPlanningRequest in memory from the initializer's planning_request_context.

Copy source_dimension_request, handoff, and publication_directory unchanged. Add unique request and plan IDs, one RFC 3339 timestamp, explicit authorization, prioritized intents, and only evidence-backed assumptions. Reference only objects and views present in the handoff. Keep all sheet, scale, view, and format authorization disabled unless the user supplied explicit approval.

Submit the same request to the semantic publication tool. After publication, reuse the exact request and exact published plan through validate, transactional create, and independent verify. If planning or validation reports capability_blocked, retain the immutable plan and stop before create; do not substitute, simplify, repair, or republish it.
