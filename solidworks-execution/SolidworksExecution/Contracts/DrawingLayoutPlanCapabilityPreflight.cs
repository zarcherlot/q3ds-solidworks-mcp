using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;

namespace SolidworksExecution.Contracts
{
    /// <summary>G4 production promotion gate for operations, safety and G0 boundaries.</summary>
    public sealed class DrawingLayoutPlanCapabilityPreflight
    {
        private static readonly string[] MandatorySafety = { "dimension_semantic_preservation",
            "view_semantic_preservation", "object_identity_preservation", "collision_readback",
            "save_reopen_layout_fingerprint", "authorized_sheet_change" };

        public bool TryValidate(DrawingLayoutExecutionPlan plan, string registryPath,
            string boundaryRegistryPath, out DrawingLayoutPlanContractError error)
        { return TryValidateCore(plan, registryPath, boundaryRegistryPath, false, out error); }

        public bool TryValidateQualification(DrawingLayoutExecutionPlan plan,
            string registryPath, string boundaryRegistryPath,
            out DrawingLayoutPlanContractError error)
        { return TryValidateCore(plan, registryPath, boundaryRegistryPath, true, out error); }

        private bool TryValidateCore(DrawingLayoutExecutionPlan plan, string registryPath,
            string boundaryRegistryPath, bool qualification,
            out DrawingLayoutPlanContractError error)
        {
            error = null; JObject registry, boundaries;
            try { registry = DrawingLayoutPlanTransactionPreflight.Load(registryPath);
                boundaries = DrawingLayoutPlanTransactionPreflight.Load(boundaryRegistryPath); }
            catch (Exception ex) { return Fail("DRAWING_LAYOUT_CAPABILITY_REGISTRY_INVALID", "",
                ex.Message, out error); }
            if (registry.Value<string>("protocol_id") !=
                    "solidworks-drawing-layout-plan-capabilities" ||
                registry.Value<string>("schema_version") != "1.0" ||
                registry.Value<string>("plan_protocol_id") !=
                    DrawingLayoutPlanContractValidator.ProtocolId ||
                registry.Value<string>("plan_schema_version") !=
                    DrawingLayoutPlanContractValidator.SchemaVersion ||
                registry.Value<string>("solidworks_target") != "2025 SP5" ||
                String.IsNullOrWhiteSpace(registry.Value<string>("solidworks_revision")))
                return Fail("DRAWING_LAYOUT_CAPABILITY_REGISTRY_INVALID", "",
                    "Registry is not bound to DrawingLayoutPlan 1.0.", out error);
            JObject boundaryBinding = registry["boundary_registry"] as JObject;
            if (boundaryBinding == null || boundaries.Value<string>("protocol_id") !=
                    boundaryBinding.Value<string>("protocol_id") ||
                boundaries.Value<string>("registry_version") !=
                    boundaryBinding.Value<string>("registry_version") ||
                boundaries.Value<string>("solidworks_revision") !=
                    registry.Value<string>("solidworks_revision") ||
                boundaries.Value<string>("verification") != "live_complete" ||
                !IsHash(boundaries.SelectToken("live_evidence.qualification_sha256")
                    ?.Value<string>()) ||
                !String.Equals(DrawingLayoutPlanContractValidator.FileSha256(boundaryRegistryPath),
                    boundaryBinding.Value<string>("manifest_sha256"),
                    StringComparison.OrdinalIgnoreCase))
                return Fail("DRAWING_LAYOUT_BOUNDARY_REGISTRY_MISMATCH", "",
                    "G2 registry does not hash-bind the supplied G0 registry.", out error);
            JObject operations = registry["operations"] as JObject;
            foreach (string kind in plan.Operations.Select(item => item.Kind).Distinct(
                StringComparer.Ordinal))
                if (qualification
                    ? !QualificationEligible(operations != null ? operations[kind] as JObject : null)
                    : !Supported(operations != null ? operations[kind] as JObject : null))
                    return Fail(qualification ? "DRAWING_LAYOUT_QUALIFICATION_BLOCKED" :
                        "DRAWING_LAYOUT_CAPABILITY_BLOCKED", "/operations",
                        qualification ? "Native operation is known-unsupported: " + kind + "." :
                        "Native operation is not live-supported: " + kind + ".", out error);
            JObject safety = registry["safety_elements"] as JObject;
            foreach (string id in MandatorySafety)
                if (qualification
                    ? !QualificationEligible(safety != null ? safety[id] as JObject : null)
                    : !Supported(safety != null ? safety[id] as JObject : null))
                    return Fail(qualification ? "DRAWING_LAYOUT_QUALIFICATION_BLOCKED" :
                        "DRAWING_LAYOUT_CAPABILITY_BLOCKED", "/execution_policy",
                        qualification ? "Safety readback is known-unsupported: " + id + "." :
                        "Safety readback is not live-supported: " + id + ".", out error);
            var states = new Dictionary<string, string>(StringComparer.Ordinal);
            JArray rows = boundaries["capabilities"] as JArray;
            if (rows == null) return Fail("DRAWING_LAYOUT_CAPABILITY_REGISTRY_INVALID", "",
                "G0 registry has no capabilities.", out error);
            foreach (JObject row in rows.OfType<JObject>())
            {
                string id = row.Value<string>("id");
                if (String.IsNullOrEmpty(id) || states.ContainsKey(id))
                    return Fail("DRAWING_LAYOUT_CAPABILITY_REGISTRY_INVALID", "",
                        "G0 capability IDs must be unique.", out error);
                states.Add(id, row.Value<string>("status"));
            }
            foreach (string id in plan.RequiredBoundaryCapabilities)
            {
                string state;
                if (!states.TryGetValue(id, out state) || state != "supported")
                    return Fail(qualification ? "DRAWING_LAYOUT_QUALIFICATION_BLOCKED" :
                        "DRAWING_LAYOUT_BOUNDARY_CAPABILITY_BLOCKED",
                        "/source_invariants/required_boundary_capabilities",
                        "Required exact G0 boundary is not supported: " + id + ".", out error);
            }
            return true;
        }

        private static bool Supported(JObject item) => item != null &&
            item.Value<string>("status") == "supported" &&
            item.Value<string>("verification") == "live" &&
            IsHash(item.Value<string>("evidence_sha256"));
        private static bool QualificationEligible(JObject item) => item != null &&
            (item.Value<string>("status") == "planned" ||
             item.Value<string>("status") == "supported");
        private static bool IsHash(string value) => value != null && value.Length == 64 &&
            value.All(c => (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'));
        private static bool Fail(string code, string pointer, string message,
            out DrawingLayoutPlanContractError error)
        { error = new DrawingLayoutPlanContractError { Code = code, JsonPointer = pointer,
            Message = message }; return false; }
    }
}
