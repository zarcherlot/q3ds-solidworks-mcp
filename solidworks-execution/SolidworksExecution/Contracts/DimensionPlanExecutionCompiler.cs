using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;

namespace SolidworksExecution.Contracts
{
    /// <summary>
    /// Fail-closed F4 compiler. It accepts only the native MVP; all F5 dimension kinds and
    /// tolerance formatting are rejected instead of being translated to DrawingPlan 1.0.
    /// </summary>
    public sealed class DimensionPlanExecutionCompiler
    {
        private static readonly HashSet<string> SupportedKinds = new HashSet<string>(
            new[] { "linear", "diameter", "radius", "angular", "reference",
                "hole_diameter", "hole_depth", "hole_quantity" }, StringComparer.Ordinal);

        public bool TryCompile(DimensionPlanDocument document,
            out DimensionPlanExecutionPlan plan, out DimensionPlanContractError error)
        {
            plan = null;
            error = null;
            if (document == null || document.CanonicalPlan == null)
                return Fail("DIMENSION_PLAN_COMPILE_INVALID", "", "DimensionPlan is required.",
                    out error);

            JObject root = document.CanonicalPlan;
            var compiled = new DimensionPlanExecutionPlan
            {
                PlanId = document.PlanId,
                PlanSha256 = document.CanonicalSha256,
                Plan = (JObject)root.DeepClone(),
                HandoffId = root.Value<string>("handoff_id"),
                Configuration = root.Value<string>("configuration"),
                Handoff = Artifact(root["handoff"]),
                SourceModel = Artifact(root["source_model"]),
                SourceDrawing = Artifact(root["source_drawing"]),
                ViewPlan = Artifact(root["view_plan"]),
                VerificationSidecar = Artifact(root["verification_sidecar"]),
                Dimensions = new List<DimensionPlanExecutionDimension>()
            };

            JArray dimensions = (JArray)root["dimensions"];
            for (int index = 0; index < dimensions.Count; index++)
            {
                JObject item = (JObject)dimensions[index];
                string pointer = "/dimensions/" + index;
                string kind = item.Value<string>("kind");
                if (!SupportedKinds.Contains(kind))
                    return Fail("DIMENSION_CAPABILITY_BLOCKED", pointer + "/kind",
                        "Dimension kind is outside the F4 native MVP: " + kind, out error);
                if (item["tolerance"].Type != JTokenType.Null)
                    return Fail("DIMENSION_CAPABILITY_BLOCKED", pointer + "/tolerance",
                        "Native tolerance formatting is reserved for F5.", out error);

                JObject source = (JObject)item["source"];
                string tier = source.Value<string>("source_tier");
                string collection = source.Value<string>("handoff_collection");
                var sourceIds = ReadStrings(source["source_ids"] ?? source["approved_input_ids"] ??
                    source["measurement_ids"]);
                bool importModelDimension = string.Equals(tier, "model_or_pmi",
                    StringComparison.Ordinal) && string.Equals(collection,
                    "model_driven_dimensions", StringComparison.Ordinal);
                if (string.Equals(tier, "model_or_pmi", StringComparison.Ordinal) &&
                    !importModelDimension && !string.Equals(collection,
                    "manufacturing_features", StringComparison.Ordinal))
                    return Fail("DIMENSION_CAPABILITY_BLOCKED", pointer + "/source",
                        "F4 supports model dimensions and basic manufacturing-feature callouts only.",
                        out error);
                if (importModelDimension && sourceIds.Count != 1)
                    return Fail("DIMENSION_PLAN_COMPILE_INVALID", pointer + "/source/source_ids",
                        "One planned display dimension must bind exactly one model dimension.", out error);

                var attachments = new List<DimensionPlanExecutionAttachment>();
                foreach (JObject attachment in ((JArray)item["attachments"]).OfType<JObject>())
                {
                    if (!string.Equals(attachment.Value<string>("persistent_reference_kind"),
                        "entity", StringComparison.Ordinal))
                        return Fail("DIMENSION_CAPABILITY_BLOCKED", pointer + "/attachments",
                            "F4 cannot create dimensions from backing-face silhouette references.",
                            out error);
                    attachments.Add(new DimensionPlanExecutionAttachment
                    {
                        AttachmentId = attachment.Value<string>("attachment_id"),
                        EntityId = attachment.Value<string>("entity_id"),
                        PersistentReference = attachment.Value<string>("model_persistent_reference"),
                        Role = attachment.Value<string>("role")
                    });
                }
                int expected = (kind == "linear" || kind == "angular") ? 2 : 1;
                if (kind != "reference" && attachments.Count != expected)
                    return Fail("DIMENSION_PLAN_COMPILE_INVALID", pointer + "/attachments",
                        kind + " requires exactly " + expected + " attachment(s) in F4.", out error);
                if (kind == "reference" && (attachments.Count < 1 || attachments.Count > 2))
                    return Fail("DIMENSION_PLAN_COMPILE_INVALID", pointer + "/attachments",
                        "reference requires one or two attachments in F4.", out error);

                JObject display = (JObject)item["display_format"];
                if (display.Value<bool>("dual_units") || display.Value<bool>("show_units"))
                    return Fail("DIMENSION_CAPABILITY_BLOCKED", pointer + "/display_format",
                        "F4 uses document units and does not synthesize unit or dual-unit text.", out error);

                JArray position = (JArray)item["initial_position_sheet_m"];
                JObject value = (JObject)item["value"];
                string quantityKind = value.Value<string>("quantity_kind");
                string expectedQuantity = kind == "angular" ? "angle" :
                    (kind == "hole_quantity" ? "count" : "length");
                if (quantityKind != expectedQuantity)
                    return Fail("DIMENSION_PLAN_COMPILE_INVALID", pointer + "/value/quantity_kind",
                        kind + " requires quantity_kind=" + expectedQuantity + ".", out error);
                double nominal = value.Value<double>("nominal_si");
                if (kind == "hole_quantity" && Math.Abs(nominal - Math.Round(nominal)) > 1e-9)
                    return Fail("DIMENSION_PLAN_COMPILE_INVALID", pointer + "/value/nominal_si",
                        "hole_quantity nominal_si must be an integer count.", out error);
                JObject verification = (JObject)item["verification_tolerance"];
                if (verification.Value<bool>("display_text_exact"))
                    return Fail("DIMENSION_CAPABILITY_BLOCKED",
                        pointer + "/verification_tolerance/display_text_exact",
                        "Exact rendered glyph text is not supported by the SolidWorks 2025 SP5 API.",
                        out error);
                compiled.Dimensions.Add(new DimensionPlanExecutionDimension
                {
                    DimensionId = item.Value<string>("dimension_id"), Kind = kind,
                    SourceTier = tier, HandoffCollection = collection, SourceIds = sourceIds,
                    TargetViewId = item.Value<string>("target_view_id"), Attachments = attachments,
                    NominalSi = nominal,
                    QuantityKind = quantityKind,
                    ValueMode = value.Value<string>("value_mode"),
                    Prefix = display.Value<string>("prefix") ?? "",
                    Suffix = display.Value<string>("suffix") ?? "",
                    Unit = display.Value<string>("unit"),
                    Precision = display.Value<int>("precision"),
                    ShowParentheses = display.Value<bool>("show_parentheses"),
                    PositionX = position.Value<double>(0), PositionY = position.Value<double>(1),
                    ValueTolerance = verification.Value<double>("value_abs_si"),
                    PositionTolerance = verification.Value<double>("position_abs_m"),
                    DisplayTextExact = verification.Value<bool>("display_text_exact"),
                    ImportModelDimension = importModelDimension
                });
            }
            plan = compiled;
            return true;
        }

        private static DimensionPlanArtifactBinding Artifact(JToken token)
        {
            var value = (JObject)token;
            return new DimensionPlanArtifactBinding
                { Path = value.Value<string>("path"), Sha256 = value.Value<string>("sha256") };
        }

        private static List<string> ReadStrings(JToken token)
        {
            return token == null ? new List<string>() :
                ((JArray)token).Values<string>().ToList();
        }

        private static bool Fail(string code, string pointer, string message,
            out DimensionPlanContractError error)
        {
            error = new DimensionPlanContractError
                { Code = code, JsonPointer = pointer, Message = message };
            return false;
        }
    }

    public sealed class DimensionPlanExecutionPlan
    {
        public string PlanId, PlanSha256, HandoffId, Configuration;
        public JObject Plan;
        public DimensionPlanArtifactBinding Handoff, SourceModel, SourceDrawing, ViewPlan,
            VerificationSidecar;
        public List<DimensionPlanExecutionDimension> Dimensions;
    }

    public sealed class DimensionPlanArtifactBinding { public string Path, Sha256; }

    public sealed class DimensionPlanExecutionAttachment
    {
        public string AttachmentId, EntityId, PersistentReference, Role;
    }

    public sealed class DimensionPlanExecutionDimension
    {
        public string DimensionId, Kind, SourceTier, HandoffCollection, TargetViewId,
            QuantityKind, ValueMode, Prefix, Suffix, Unit, TargetViewName, ModelDimensionFullName;
        public List<string> SourceIds;
        public List<DimensionPlanExecutionAttachment> Attachments;
        public double NominalSi, PositionX, PositionY, ValueTolerance, PositionTolerance;
        public int Precision;
        public bool ShowParentheses, DisplayTextExact, ImportModelDimension;
    }
}
