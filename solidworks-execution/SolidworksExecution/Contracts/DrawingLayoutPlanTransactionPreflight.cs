using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace SolidworksExecution.Contracts
{
    /// <summary>G4 immutable-input, cross-artifact and no-overwrite gate.</summary>
    public sealed class DrawingLayoutPlanTransactionPreflight
    {
        public bool TryValidate(DrawingLayoutExecutionPlan plan, string planPath,
            string planSha256, string requestedOutputPath, out DrawingLayoutTransactionPaths paths,
            out DrawingLayoutPlanContractError error)
        {
            paths = null; error = null;
            if (plan == null || plan.Plan == null)
                return Fail("DRAWING_LAYOUT_PLAN_INPUT_INVALID", "", "Compiled plan is required.", out error);
            string frozenPlanPath;
            if (!TryInput(planPath, ".json", out frozenPlanPath, "/plan_path", out error))
                return false;
            if (!String.Equals(Path.GetFileName(frozenPlanPath), "drawing_layout_plan.json",
                StringComparison.OrdinalIgnoreCase))
                return Fail("DRAWING_LAYOUT_PLAN_INPUT_INVALID", "/plan_path",
                    "plan_path must name drawing_layout_plan.json.", out error);
            string actualPlanHash = DrawingLayoutPlanContractValidator.FileSha256(frozenPlanPath);
            if (!HashEquals(actualPlanHash, planSha256))
                return Fail("DRAWING_LAYOUT_PLAN_INPUT_HASH_MISMATCH", "/plan_sha256",
                    "Published DrawingLayoutPlan SHA-256 does not match plan_sha256.", out error);
            JObject diskPlan;
            try { diskPlan = Load(frozenPlanPath); }
            catch (Exception ex) { return Fail("DRAWING_LAYOUT_PLAN_INPUT_INVALID", "/plan_path",
                ex.Message, out error); }
            if (!JToken.DeepEquals(DrawingLayoutPlanContractValidator.Canonicalize(diskPlan), plan.Plan))
                return Fail("DRAWING_LAYOUT_PLAN_INPUT_MISMATCH", "/plan",
                    "Structured plan differs from the immutable publication.", out error);

            var inputs = new HashSet<string>(StringComparer.OrdinalIgnoreCase) { frozenPlanPath };
            foreach (DrawingLayoutArtifactBinding artifact in new[] { plan.Handoff,
                plan.SourceDimensionPlan, plan.SourceDrawing, plan.DimensionVerificationSidecar })
            {
                string full;
                if (!TryInput(artifact.Path, null, out full, "", out error)) return false;
                if (!inputs.Add(full))
                    return Fail("DRAWING_LAYOUT_PLAN_INPUT_INVALID", "",
                        "Every frozen layout artifact must use a distinct path.", out error);
                if (!HashEquals(DrawingLayoutPlanContractValidator.FileSha256(full), artifact.Sha256))
                    return Fail("DRAWING_LAYOUT_PLAN_INPUT_HASH_MISMATCH", "",
                        "Frozen artifact SHA-256 mismatch: " + full, out error);
                artifact.Path = full;
            }
            if (!String.Equals(Path.GetExtension(plan.SourceDrawing.Path), ".SLDDRW",
                StringComparison.OrdinalIgnoreCase))
                return Fail("DRAWING_LAYOUT_PLAN_INPUT_INVALID", "/source_drawing/path",
                    "source_drawing must be a .SLDDRW file.", out error);

            JObject handoff, dimensionPlan, sidecar;
            try { handoff = Load(plan.Handoff.Path); dimensionPlan = Load(plan.SourceDimensionPlan.Path);
                sidecar = Load(plan.DimensionVerificationSidecar.Path); }
            catch (Exception ex) { return Fail("DRAWING_LAYOUT_HANDOFF_INVALID", "/handoff",
                ex.Message, out error); }
            if (handoff.Value<string>("protocol_id") != "solidworks-drawing-layout-handoff" ||
                handoff.Value<string>("schema_version") != "1.0" ||
                (handoff.Value<string>("status") != "ready" &&
                 handoff.Value<string>("status") != "capability_blocked") ||
                handoff.Value<string>("handoff_id") != plan.HandoffId)
                return Fail("DRAWING_LAYOUT_HANDOFF_INVALID", "/handoff",
                    "G4 requires the exact immutable G1 handoff.", out error);
            if (dimensionPlan.Value<string>("protocol_id") != "solidworks-dimension-plan" ||
                dimensionPlan.Value<string>("schema_version") != "1.0" ||
                dimensionPlan.Value<string>("configuration") != plan.Configuration)
                return Fail("DRAWING_LAYOUT_DIMENSION_PLAN_MISMATCH", "/source_dimension_plan",
                    "The upstream DimensionPlan protocol or configuration differs.", out error);
            foreach (string name in new[] { "handoff", "source_model", "source_drawing",
                "view_plan", "verification_sidecar" })
            {
                JObject value = dimensionPlan[name] as JObject;
                string nestedPath, nestedHash = value != null ? value.Value<string>("sha256") : null;
                if (value == null || !TryInput(value.Value<string>("path"), null,
                    out nestedPath, "/source_dimension_plan/" + name, out error)) return false;
                if (!inputs.Add(nestedPath) || !HashEquals(
                    DrawingLayoutPlanContractValidator.FileSha256(nestedPath), nestedHash))
                    return Fail("DRAWING_LAYOUT_DIMENSION_PLAN_MISMATCH",
                        "/source_dimension_plan/" + name,
                        "Nested DimensionPlan artifact is duplicated or hash-mismatched.", out error);
                plan.UpstreamDimensionArtifacts[name] = new DrawingLayoutArtifactBinding
                    { Path = nestedPath, Sha256 = nestedHash };
            }
            JObject semantics = handoff["dimension_semantics"] as JObject;
            JObject snapshots = handoff["snapshots"] as JObject;
            if (semantics == null || snapshots == null ||
                semantics.Value<string>("invariant_sha256") != plan.DimensionSemanticSha256 ||
                snapshots.Value<string>("readonly_reopen_sha256") != plan.ObjectSnapshotSha256)
                return Fail("DRAWING_LAYOUT_INVARIANT_MISMATCH", "/source_invariants",
                    "Frozen semantic or object snapshot hash differs from G1.", out error);
            if (!SetEquals(plan.DimensionIds, ((JArray)semantics["dimensions"])
                .OfType<JObject>().Select(row => row.Value<string>("dimension_id"))) ||
                !SetEquals(plan.ObjectIds, ((JArray)handoff["objects"])
                .OfType<JObject>().Select(row => row.Value<string>("id"))) ||
                !SetEquals(plan.ViewNames, ((JArray)handoff.SelectToken(
                    "constraints.view_constraints")).OfType<JObject>()
                    .Select(row => row.Value<string>("view"))) ||
                !SetEquals(plan.LockedObjectIds, ((JArray)handoff.SelectToken(
                    "constraints.frozen_objects")).Values<string>()) ||
                !SetEquals(plan.RequiredBoundaryCapabilities, ((JArray)handoff.SelectToken(
                    "boundary_capabilities.required")).Values<string>()))
                return Fail("DRAWING_LAYOUT_INVARIANT_MISMATCH", "/source_invariants",
                    "Frozen IDs or required boundary capabilities differ from G1.", out error);

            JArray ledger = handoff["upstream_artifacts"] as JArray;
            if (ledger == null || ledger.Count != 5 || !Ledger(ledger, "dimension_plan",
                    plan.SourceDimensionPlan) || !Ledger(ledger, "dimensioned_drawing",
                    plan.SourceDrawing) || !Ledger(ledger, "dimension_verification_sidecar",
                    plan.DimensionVerificationSidecar))
                return Fail("DRAWING_LAYOUT_HANDOFF_LEDGER_MISMATCH", "/handoff/upstream_artifacts",
                    "G1 upstream ledger does not bind the frozen layout inputs.", out error);
            if (sidecar.Value<string>("protocol_id") != "solidworks-dimension-drawing-verification" ||
                sidecar.Value<bool?>("verified") != true ||
                sidecar.Value<string>("plan_id") != dimensionPlan.Value<string>("plan_id") ||
                !PathEquals(sidecar.Value<string>("output_path"), plan.SourceDrawing.Path) ||
                !HashEquals(sidecar.Value<string>("artifact_sha256"), plan.SourceDrawing.Sha256) ||
                !PathEquals(sidecar.Value<string>("plan_file_path"), plan.SourceDimensionPlan.Path) ||
                !HashEquals(sidecar.Value<string>("plan_file_sha256"), plan.SourceDimensionPlan.Sha256))
                return Fail("DRAWING_LAYOUT_DIMENSION_SIDECAR_MISMATCH",
                    "/dimension_verification_sidecar",
                    "Dimension verification sidecar does not bind the upstream drawing and plan.", out error);
            // G1 already validates the Python-canonical plan hash before publishing the
            // immutable handoff. G4 independently rechecks the exact plan and sidecar file
            // hashes above; recomputing that cross-language float serialization here would
            // reject valid doubles whose shortest round-trip spelling differs in Json.NET.

            string output;
            try { output = Path.GetFullPath(requestedOutputPath ?? ""); }
            catch (Exception ex) { return Fail("DRAWING_LAYOUT_OUTPUT_INVALID", "/output_path",
                ex.Message, out error); }
            if (String.IsNullOrWhiteSpace(requestedOutputPath) || !Path.IsPathRooted(requestedOutputPath) ||
                !String.Equals(Path.GetExtension(output), ".SLDDRW", StringComparison.OrdinalIgnoreCase) ||
                inputs.Contains(output) || !Directory.Exists(Path.GetDirectoryName(output)))
                return Fail("DRAWING_LAYOUT_OUTPUT_INVALID", "/output_path",
                    "output_path must be a new absolute .SLDDRW in an existing directory.", out error);
            string report = output + ".layout-verification.json";
            if (inputs.Contains(report) || File.Exists(output) || File.Exists(report))
                return Fail("DRAWING_LAYOUT_OUTPUT_EXISTS", "/output_path",
                    "G4 never overwrites a drawing or verification sidecar.", out error);
            plan.HandoffValue = handoff;
            paths = new DrawingLayoutTransactionPaths { PlanPath = frozenPlanPath,
                PlanFileSha256 = actualPlanHash, OutputPath = output, ReportPath = report };
            return true;
        }

        private static bool Ledger(JArray ledger, string role, DrawingLayoutArtifactBinding artifact)
        {
            JObject row = ledger.OfType<JObject>().SingleOrDefault(item =>
                item.Value<string>("role") == role);
            return row != null && PathEquals(row.Value<string>("path"), artifact.Path) &&
                HashEquals(row.Value<string>("sha256_before"), artifact.Sha256) &&
                HashEquals(row.Value<string>("sha256_after"), artifact.Sha256);
        }
        private static bool SetEquals(HashSet<string> expected, IEnumerable<string> actual) =>
            expected != null && expected.SetEquals(actual ?? Enumerable.Empty<string>());
        private static bool TryInput(string path, string extension, out string full,
            string pointer, out DrawingLayoutPlanContractError error)
        {
            full = null; error = null;
            try
            {
                if (String.IsNullOrWhiteSpace(path) || !Path.IsPathRooted(path))
                    throw new InvalidDataException("Path must be absolute.");
                full = Path.GetFullPath(path);
                if (!File.Exists(full)) throw new FileNotFoundException("File was not found.", full);
                if (extension != null && !String.Equals(Path.GetExtension(full), extension,
                    StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException(
                        "Unexpected file extension.");
                return true;
            }
            catch (Exception ex) { return Fail("DRAWING_LAYOUT_PLAN_INPUT_INVALID", pointer,
                ex.Message, out error); }
        }
        internal static JObject Load(string path)
        {
            using (var stream = File.OpenText(path))
            using (var reader = new JsonTextReader(stream) { DateParseHandling = DateParseHandling.None })
            {
                JObject value = JObject.Load(reader, new JsonLoadSettings
                    { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
                if (reader.Read()) throw new InvalidDataException("Trailing JSON is not allowed.");
                return value;
            }
        }
        private static bool PathEquals(string first, string second)
        { try { return String.Equals(Path.GetFullPath(first), Path.GetFullPath(second),
            StringComparison.OrdinalIgnoreCase); } catch { return false; } }
        private static bool HashEquals(string first, string second) => String.Equals(first,
            second, StringComparison.OrdinalIgnoreCase);
        private static bool Fail(string code, string pointer, string message,
            out DrawingLayoutPlanContractError error)
        { error = new DrawingLayoutPlanContractError { Code = code, JsonPointer = pointer,
            Message = message }; return false; }
    }

    public sealed class DrawingLayoutTransactionPaths
    { public string PlanPath, PlanFileSha256, OutputPath, ReportPath; }
}
