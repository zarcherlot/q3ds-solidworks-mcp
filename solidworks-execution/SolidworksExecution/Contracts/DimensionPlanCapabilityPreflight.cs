using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace SolidworksExecution.Contracts
{
    /// <summary>
    /// Production promotion gate. Implemented code is not execution support: every required
    /// dimension/element must carry live readback evidence in the versioned registry.
    /// </summary>
    public sealed class DimensionPlanCapabilityPreflight
    {
        public bool TryValidate(DimensionPlanExecutionPlan plan, string registryPath,
            out DimensionPlanContractError error)
        {
            error = null;
            JObject registry;
            try
            {
                if (string.IsNullOrWhiteSpace(registryPath) || !Path.IsPathRooted(registryPath) ||
                    !File.Exists(registryPath))
                    throw new FileNotFoundException("Dimension capability registry was not found.",
                        registryPath);
                using (var stream = File.OpenText(registryPath))
                using (var reader = new JsonTextReader(stream) { DateParseHandling = DateParseHandling.None })
                {
                    registry = JObject.Load(reader, new JsonLoadSettings
                        { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error,
                            CommentHandling = CommentHandling.Ignore,
                            LineInfoHandling = LineInfoHandling.Ignore });
                    if (reader.Read()) throw new InvalidDataException("Registry contains trailing JSON.");
                }
            }
            catch (Exception ex)
            { return Fail("DIMENSION_CAPABILITY_REGISTRY_INVALID", "", ex.Message, out error); }
            if (registry.Value<string>("protocol_id") !=
                    "solidworks-dimension-executor-capabilities" ||
                registry.Value<string>("schema_version") != "1.0" ||
                registry.Value<string>("plan_protocol_id") != "solidworks-dimension-plan" ||
                registry.Value<string>("plan_schema_version") != "1.0")
                return Fail("DIMENSION_CAPABILITY_REGISTRY_INVALID", "",
                    "Capability registry is not bound to DimensionPlan 1.0.", out error);

            var requiredTypes = new HashSet<string>(plan.Dimensions.Select(item => item.Kind),
                StringComparer.Ordinal);
            var requiredElements = new HashSet<string>(new[]
                { "attachment_persistent_reference", "annotation_position",
                    "save_reopen_stable_identity" }, StringComparer.Ordinal);
            if (plan.Dimensions.Any(item => item.ImportModelDimension))
                requiredElements.Add("model_dimension_import");
            if (plan.Dimensions.Any(item => item.Prefix.Length > 0 || item.Suffix.Length > 0))
                requiredElements.Add("dimension_prefix_suffix");
            if (plan.Dimensions.Any(item => item.Tolerance != null))
                requiredElements.Add("dimension_tolerance");
            var requiredCapabilities = new HashSet<string>(new[]
                { "display_dimension_iteration", "attachment_persistent_reference",
                    "annotation_position", "save_reopen_stable_identity" }, StringComparer.Ordinal);
            foreach (string kind in requiredTypes)
            {
                if (kind == "diameter" || kind == "boss")
                    requiredCapabilities.Add("diameter_dimension");
                else if (kind == "radius" || kind == "fillet")
                    requiredCapabilities.Add("radius_dimension");
                else if (kind == "angular") requiredCapabilities.Add("angular_dimension");
                else if (kind.StartsWith("hole_", StringComparison.Ordinal))
                    requiredCapabilities.Add("hole_callout");
                else if (kind == "chamfer") requiredCapabilities.Add("chamfer_dimension");
                else requiredCapabilities.Add("linear_dimension");
            }
            if (plan.Dimensions.Any(item => item.ImportModelDimension))
                requiredCapabilities.Add("model_dimension_import");
            if (plan.Dimensions.Any(item => item.Prefix.Length > 0 || item.Suffix.Length > 0))
                requiredCapabilities.Add("dimension_prefix_suffix");
            if (plan.Dimensions.Any(item => item.Tolerance != null))
                requiredCapabilities.Add("dimension_tolerance");
            JObject types = registry["dimension_types"] as JObject;
            JObject elements = registry["elements"] as JObject;
            JObject liveEvidence = registry["live_evidence"] as JObject;
            if (liveEvidence == null || !IsHash(liveEvidence.Value<string>("summary_sha256")))
                return Fail("DIMENSION_CAPABILITY_REGISTRY_INVALID", "",
                    "Capability registry has no hash-bound live evidence summary.", out error);
            var capabilityStates = new Dictionary<string, string>(StringComparer.Ordinal);
            JArray capabilityRows = registry["capabilities"] as JArray;
            if (capabilityRows == null)
                return Fail("DIMENSION_CAPABILITY_REGISTRY_INVALID", "",
                    "Capability registry has no capability inventory.", out error);
            foreach (JObject row in capabilityRows.OfType<JObject>())
            {
                string id = row.Value<string>("id");
                if (string.IsNullOrEmpty(id) || capabilityStates.ContainsKey(id))
                    return Fail("DIMENSION_CAPABILITY_REGISTRY_INVALID", "",
                        "Capability IDs must be present and unique.", out error);
                capabilityStates.Add(id, row.Value<string>("status"));
            }
            foreach (string id in requiredCapabilities)
            {
                string status;
                if (!capabilityStates.TryGetValue(id, out status) || status != "supported")
                    return Fail("DIMENSION_CAPABILITY_BLOCKED", "/dimensions",
                        "Native capability is not live-supported: " + id + ".", out error);
            }
            foreach (string id in requiredTypes)
                if (!Supported(types != null ? types[id] as JObject : null))
                    return Fail("DIMENSION_CAPABILITY_BLOCKED", "/dimensions",
                        "Dimension capability is not live-supported: " + id + ".", out error);
            foreach (string id in requiredElements)
                if (!Supported(elements != null ? elements[id] as JObject : null))
                    return Fail("DIMENSION_CAPABILITY_BLOCKED", "/dimensions",
                        "Required execution element is not live-supported: " + id + ".", out error);
            return true;
        }

        private static bool Supported(JObject item) => item != null &&
            item.Value<string>("status") == "supported" &&
            item.Value<string>("verification") == "live" &&
            IsHash(item.Value<string>("evidence_sha256"));
        private static bool IsHash(string value) => value != null && value.Length == 64 &&
            value.All(c => (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'));
        private static bool Fail(string code, string pointer, string message,
            out DimensionPlanContractError error)
        { error = new DimensionPlanContractError { Code = code, JsonPointer = pointer, Message = message };
            return false; }
    }
}
