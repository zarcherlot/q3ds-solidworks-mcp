Plan views and sheet layout from the repository initializer's complete artifact handoff.

Upstream artifact paths:

{{UPSTREAM_ARTIFACTS_JSON}}

Repository publication target (context only; the PlannerEngine owns the write):

{{VIEW_PLAN_TARGET_JSON}}

User requirements JSON:

{{USER_REQUIREMENTS_JSON}}

Choose the main orientation, minimum view set, view types, scale, feature coverage, frozen geometry,
center elements, dimension zones, and collision-free layout. Preserve every input path and SHA-256
binding required by ViewPlan 1.4. Return only the complete schema-1.4 JSON candidate. The repository
will perform validation, capability assessment, atomic publication, and any later C# transaction.
