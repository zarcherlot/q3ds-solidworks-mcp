using System;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using SolidworksExecution.Contracts;
using SolidworksExecution.Services;

namespace LayoutContractTests
{
    internal static class Program
    {
        private static int _passed;

        private static int Main()
        {
            try
            {
                var contract = new LayoutBoundaryProbeContract();
                LayoutBoundaryProbeRequest request;
                LayoutBoundaryProbeContractError error;
                JObject valid = BuildValid();
                Assert(contract.TryParse(valid, out request, out error), Format(error));
                Assert(request.CapabilityIds.Length == 11, "catalog length");
                Assert(Math.Abs(request.ErrorBudgetMeters - 0.0005) < 1e-12,
                    "error budget was not preserved");
                Pass("valid G0 boundary request");

                JObject unknown = (JObject)valid.DeepClone();
                unknown["legacy_tool"] = "auto_dimension_drawing";
                Assert(!contract.TryParse(unknown, out request, out error) &&
                    error.JsonPointer == "/legacy_tool", "unknown field accepted");
                Pass("legacy field rejected");

                JObject relative = (JObject)valid.DeepClone();
                relative["source"]["dimension_plan"]["path"] = "dimension-plan.json";
                Assert(!contract.TryParse(relative, out request, out error),
                    "relative path accepted");
                Pass("relative upstream path rejected");

                JObject hash = (JObject)valid.DeepClone();
                hash["source"]["dimensioned_drawing"]["sha256"] = new string('A', 64);
                Assert(!contract.TryParse(hash, out request, out error),
                    "non-canonical hash accepted");
                Pass("non-canonical SHA-256 rejected");

                JObject reordered = (JObject)valid.DeepClone();
                var ids = (JArray)reordered["capability_ids"];
                JToken first = ids[0];
                ids[0] = ids[1];
                ids[1] = first;
                Assert(!contract.TryParse(reordered, out request, out error),
                    "reordered catalog accepted");
                Pass("capability catalog drift rejected");

                JObject revision = (JObject)valid.DeepClone();
                revision["required_solidworks_revision"] = "33.4.0";
                Assert(!contract.TryParse(revision, out request, out error),
                    "wrong SolidWorks revision accepted");
                Pass("revision is frozen to 33.5.0");

                JObject budget = (JObject)valid.DeepClone();
                budget["error_budget_m"] = 0.01;
                Assert(!contract.TryParse(budget, out request, out error),
                    "unbounded error budget accepted");
                Pass("error budget is bounded");

                JObject validation = (JObject)valid.DeepClone();
                validation["publication_directory"] = Path.Combine(Path.GetTempPath(),
                    "validation", "g0-output");
                Assert(!contract.TryParse(validation, out request, out error) &&
                    error.JsonPointer == "/publication_directory",
                    "validation output accepted");
                Pass("validation tree remains read-only");

                JObject collision = (JObject)valid.DeepClone();
                collision["publication_directory"] = Path.GetDirectoryName(
                    collision["source"]["dimensioned_drawing"]["path"].Value<string>());
                Assert(!contract.TryParse(collision, out request, out error),
                    "source directory output accepted");
                Pass("upstream publication collision rejected");

                string fixtureRoot = Path.Combine(Path.GetTempPath(),
                    "layout-g0-preflight-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(fixtureRoot);
                JObject fixture = BuildPreflightFixture(fixtureRoot, false);
                Assert(contract.TryParse(fixture, out request, out error), Format(error));
                Assert(contract.TryPreflight(request, out error), Format(error));
                Pass("verified dimension sidecar binding accepted");

                string badRoot = Path.Combine(Path.GetTempPath(),
                    "layout-g0-preflight-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(badRoot);
                JObject badFixture = BuildPreflightFixture(badRoot, true);
                Assert(contract.TryParse(badFixture, out request, out error), Format(error));
                Assert(!contract.TryPreflight(request, out error) &&
                    error.JsonPointer == "/source/dimension_verification_sidecar",
                    "mismatched dimension sidecar accepted");
                Pass("mismatched dimension sidecar binding rejected");

                string viewRoot = Path.Combine(Path.GetTempPath(),
                    "layout-g0-view-preflight-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(viewRoot);
                JObject viewFixture = BuildViewPreflightFixture(viewRoot, false);
                Assert(contract.TryParse(viewFixture, out request, out error), Format(error));
                Assert(request.SourceKind == "verified_view_plan_drawing",
                    "view source kind was not preserved");
                Assert(contract.TryPreflight(request, out error), Format(error));
                Pass("verified ViewPlan sidecar binding accepted");

                string badViewRoot = Path.Combine(Path.GetTempPath(),
                    "layout-g0-view-preflight-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(badViewRoot);
                JObject badViewFixture = BuildViewPreflightFixture(badViewRoot, true);
                Assert(contract.TryParse(badViewFixture, out request, out error),
                    Format(error));
                Assert(!contract.TryPreflight(request, out error) &&
                    error.JsonPointer == "/source/view_verification_sidecar",
                    "mismatched ViewPlan sidecar accepted");
                Pass("mismatched ViewPlan sidecar binding rejected");

                string layoutRoot = Path.Combine(Path.GetTempPath(),
                    "layout-g0-fixture-preflight-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(layoutRoot);
                JObject layoutFixture = BuildLayoutFixture(layoutRoot, false);
                Assert(contract.TryParse(layoutFixture, out request, out error), Format(error));
                Assert(contract.TryPreflight(request, out error), Format(error));
                Pass("verified layout fixture binding accepted");

                string badLayoutRoot = Path.Combine(Path.GetTempPath(),
                    "layout-g0-fixture-preflight-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(badLayoutRoot);
                JObject badLayoutFixture = BuildLayoutFixture(badLayoutRoot, true);
                Assert(contract.TryParse(badLayoutFixture, out request, out error), Format(error));
                Assert(!contract.TryPreflight(request, out error) &&
                    error.JsonPointer == "/source/layout_fixture_manifest",
                    "drifting layout fixture accepted");
                Pass("drifting layout fixture rejected");

                var g1Contract = new LayoutPlanningHandoffContract();
                LayoutPlanningHandoffRequest g1Request;
                LayoutPlanningHandoffContractError g1Error;
                string g1Root = Path.Combine(Path.GetTempPath(),
                    "layout-g1-preflight-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(g1Root);
                JObject g1 = BuildG1Fixture(g1Root, false);
                Assert(g1Contract.TryParse(g1, out g1Request, out g1Error),
                    Format(g1Error));
                Assert(g1Contract.TryPreflight(g1Request, out g1Error),
                    Format(g1Error));
                Pass("verified G1 immutable source accepted");

                JObject g1Unknown = (JObject)g1.DeepClone();
                g1Unknown["legacy_layout"] = true;
                Assert(!g1Contract.TryParse(g1Unknown, out g1Request, out g1Error) &&
                    g1Error.JsonPointer == "/legacy_layout", "G1 unknown field accepted");
                Pass("G1 unknown field rejected");

                JObject g1Spacing = (JObject)g1.DeepClone();
                g1Spacing["minimum_spacing_m"]["object_to_frame"] = 0.05;
                Assert(!g1Contract.TryParse(g1Spacing, out g1Request, out g1Error),
                    "unsafe G1 spacing accepted");
                Pass("G1 minimum spacing is bounded");

                string badG1Root = Path.Combine(Path.GetTempPath(),
                    "layout-g1-preflight-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(badG1Root);
                JObject badG1 = BuildG1Fixture(badG1Root, true);
                Assert(g1Contract.TryParse(badG1, out g1Request, out g1Error),
                    Format(g1Error));
                Assert(!g1Contract.TryPreflight(g1Request, out g1Error) &&
                    g1Error.JsonPointer == "/source/dimension_verification_sidecar",
                    "G1 dimension semantic drift accepted");
                Pass("G1 dimension semantic drift rejected");

                var arrowPoints = new System.Collections.Generic.List<double[]>();
                Assert(LayoutDisplayGeometry.AddArrowHead(arrowPoints,
                    new[] { 1.0, 2.0, 0.0, 1.0, 0.0, 0.0, 0.4, 0.2 }) &&
                    arrowPoints.Count == 3 &&
                    Math.Abs(arrowPoints.Min(point => point[0]) - 0.6) < 1e-12,
                    "native arrow width/height was not converted to an exact envelope");
                Pass("native arrow envelope is exact");

                var textPoints = new System.Collections.Generic.List<double[]>();
                Assert(LayoutDisplayGeometry.AddTextRectangle(textPoints,
                    1.0, 2.0, 4.0, 3.0, 0.0, 1) &&
                    textPoints.Min(point => point[0]) == 1.0 &&
                    textPoints.Max(point => point[0]) == 5.0 &&
                    textPoints.Min(point => point[1]) == 2.0 &&
                    textPoints.Max(point => point[1]) == 5.0,
                    "lower-left native text reference was treated as a center");
                Pass("native text reference controls the envelope");

                double[] sectionInfo =
                {
                    1, -1, 1, 4,
                    -0.077928, 0, 0, 0.077928, 0, 0,
                    0.027072, 0.1, 0, 0.027072, 0.0873, 0,
                    0.00635, 0.002032, 1,
                    0.182928, 0.1, 0, 0.182928, 0.0873, 0,
                    0.00635, 0.002032, 1,
                    0.025327781532306, 0.0873, 0,
                    0.181183781532306, 0.0873, 0, 0.003175
                };
                var sections = LayoutDisplayGeometry.ParseSectionLineInfo2(
                    sectionInfo, 0.105, 0.1);
                Assert(sections.Count == 1 && sections[0].Exact &&
                    sections[0].Points.Min(point => point[0]) > 0.02 &&
                    sections[0].Points.Max(point => point[0]) < 0.19 &&
                    sections[0].Points.Min(point => point[1]) > 0.08,
                    "section data was not parsed in parent-view and sheet frames");
                Pass("section line native structure is exact");

                Console.WriteLine("Layout G0/G1 contract tests passed: " + _passed + "/22");
                return 0;
            }
            catch (Exception exception)
            {
                Console.Error.WriteLine(exception);
                return 1;
            }
        }

        private static JObject BuildValid()
        {
            string root = Path.Combine(Path.GetTempPath(), "layout-g0-contract");
            string hash = new string('a', 64);
            return new JObject
            {
                ["protocol_id"] = LayoutBoundaryProbeContract.ProtocolId,
                ["schema_version"] = LayoutBoundaryProbeContract.SchemaVersion,
                ["source"] = new JObject
                {
                    ["kind"] = "verified_dimension_drawing",
                    ["dimension_plan"] = Artifact(Path.Combine(root,
                        "dimension-plan.json"), hash),
                    ["dimensioned_drawing"] = Artifact(Path.Combine(root,
                        "dimensioned.SLDDRW"), hash),
                    ["dimension_verification_sidecar"] = Artifact(Path.Combine(root,
                        "dimensioned.verify.json"), hash)
                },
                ["publication_directory"] = Path.Combine(root, "probe-output"),
                ["required_solidworks_revision"] =
                    LayoutBoundaryProbeContract.RequiredSolidWorksRevision,
                ["error_budget_m"] = 0.0005,
                ["capability_ids"] = new JArray(LayoutBoundaryProbeContract.CapabilityIds)
            };
        }

        private static JObject Artifact(string path, string hash)
        {
            return new JObject { ["path"] = path, ["sha256"] = hash };
        }

        private static JObject BuildPreflightFixture(string root, bool mismatch)
        {
            string planPath = Path.Combine(root, "dimension-plan.json");
            string drawingPath = Path.Combine(root, "dimensioned.SLDDRW");
            string sidecarPath = Path.Combine(root, "dimensioned.verify.json");
            string planId = "DP-G0-CONTRACT";
            File.WriteAllText(planPath, new JObject
                { ["plan_id"] = planId }.ToString());
            File.WriteAllBytes(drawingPath, new byte[] { 1, 2, 3, 4 });
            string planHash = FileSha256(planPath);
            string drawingHash = FileSha256(drawingPath);
            var sidecar = new JObject
            {
                ["protocol_id"] = "solidworks-dimension-drawing-verification",
                ["schema_version"] = "1.0",
                ["verified"] = true,
                ["plan_id"] = planId,
                ["plan_file_path"] = planPath,
                ["plan_file_sha256"] = mismatch ? new string('f', 64) : planHash,
                ["output_path"] = drawingPath,
                ["artifact_sha256"] = drawingHash,
                ["in_memory_verification"] = new JObject { ["verified"] = true },
                ["reopen_verification"] = new JObject { ["verified"] = true },
                ["frozen_inputs"] = new JObject { ["dimension_plan"] = planHash }
            };
            File.WriteAllText(sidecarPath, sidecar.ToString());
            string sidecarHash = FileSha256(sidecarPath);
            return new JObject
            {
                ["protocol_id"] = LayoutBoundaryProbeContract.ProtocolId,
                ["schema_version"] = LayoutBoundaryProbeContract.SchemaVersion,
                ["source"] = new JObject
                {
                    ["kind"] = "verified_dimension_drawing",
                    ["dimension_plan"] = Artifact(planPath, planHash),
                    ["dimensioned_drawing"] = Artifact(drawingPath, drawingHash),
                    ["dimension_verification_sidecar"] = Artifact(sidecarPath,
                        sidecarHash)
                },
                ["publication_directory"] = Path.Combine(root, "probe-output"),
                ["required_solidworks_revision"] =
                    LayoutBoundaryProbeContract.RequiredSolidWorksRevision,
                ["error_budget_m"] = 0.0005,
                ["capability_ids"] = new JArray(LayoutBoundaryProbeContract.CapabilityIds)
            };
        }

        private static JObject BuildViewPreflightFixture(string root, bool mismatch)
        {
            string planPath = Path.Combine(root, "view-plan.json");
            string drawingPath = Path.Combine(root, "view.SLDDRW");
            string sidecarPath = Path.Combine(root, "view.verify.json");
            var plan = new JObject
            {
                ["protocol_id"] = "solidworks-view-plan",
                ["schema_version"] = "1.4",
                ["plan_id"] = "VP-G0-CONTRACT"
            };
            File.WriteAllText(planPath, plan.ToString());
            File.WriteAllBytes(drawingPath, new byte[] { 4, 3, 2, 1 });
            string planHash = CanonicalSha256(plan);
            string drawingHash = FileSha256(drawingPath);
            var sidecar = new JObject
            {
                ["schema_version"] = "1.0",
                ["verified"] = true,
                ["plan_id"] = "VP-G0-CONTRACT",
                ["plan_canonical_sha256"] = mismatch ? new string('f', 64) : planHash,
                ["artifact_sha256"] = drawingHash,
                ["verification"] = new JObject
                {
                    ["verified"] = true,
                    ["views"] = new JArray(new JObject { ["id"] = "front" })
                }
            };
            File.WriteAllText(sidecarPath, sidecar.ToString());
            return new JObject
            {
                ["protocol_id"] = LayoutBoundaryProbeContract.ProtocolId,
                ["schema_version"] = LayoutBoundaryProbeContract.SchemaVersion,
                ["source"] = new JObject
                {
                    ["kind"] = "verified_view_plan_drawing",
                    ["view_plan"] = Artifact(planPath, FileSha256(planPath)),
                    ["view_drawing"] = Artifact(drawingPath, drawingHash),
                    ["view_verification_sidecar"] = Artifact(sidecarPath,
                        FileSha256(sidecarPath))
                },
                ["publication_directory"] = Path.Combine(root, "probe-output"),
                ["required_solidworks_revision"] =
                    LayoutBoundaryProbeContract.RequiredSolidWorksRevision,
                ["error_budget_m"] = 0.0005,
                ["capability_ids"] = new JArray(LayoutBoundaryProbeContract.CapabilityIds)
            };
        }

        private static JObject BuildLayoutFixture(string root, bool drift)
        {
            string drawingPath = Path.Combine(root, "fixture.SLDDRW");
            string sidecarPath = Path.Combine(root, "source.verify.json");
            string manifestPath = Path.Combine(root, "layout-fixture.json");
            File.WriteAllBytes(drawingPath, new byte[] { 9, 8, 7, 6 });
            File.WriteAllText(sidecarPath, new JObject { ["verified"] = true }.ToString());
            string drawingHash = FileSha256(drawingPath);
            string sidecarHash = FileSha256(sidecarPath);
            var manifest = new JObject
            {
                ["protocol_id"] = "solidworks-layout-g0-title-block-fixture",
                ["schema_version"] = "1.0",
                ["verified"] = true,
                ["solidworks_revision"] = "33.5.0",
                ["fixture_drawing_path"] = drawingPath,
                ["fixture_drawing_sha256"] = drawingHash,
                ["source_verification_sidecar_sha256"] = sidecarHash,
                ["title_block"] = new JObject
                {
                    ["native_api"] = "ITitleBlock.GetExtents",
                    ["before_extents_m"] = new JArray(0.32, 0.075, 0.41, 0.01),
                    ["reopen_extents_m"] = new JArray(0.32, 0.075,
                        drift ? 0.40 : 0.41, 0.01)
                }
            };
            File.WriteAllText(manifestPath, manifest.ToString());
            return new JObject
            {
                ["protocol_id"] = LayoutBoundaryProbeContract.ProtocolId,
                ["schema_version"] = LayoutBoundaryProbeContract.SchemaVersion,
                ["source"] = new JObject
                {
                    ["kind"] = "verified_layout_fixture",
                    ["layout_fixture_manifest"] = Artifact(manifestPath,
                        FileSha256(manifestPath)),
                    ["fixture_drawing"] = Artifact(drawingPath, drawingHash),
                    ["source_verification_sidecar"] = Artifact(sidecarPath, sidecarHash)
                },
                ["publication_directory"] = Path.Combine(root, "probe-output"),
                ["required_solidworks_revision"] =
                    LayoutBoundaryProbeContract.RequiredSolidWorksRevision,
                ["error_budget_m"] = 0.0005,
                ["capability_ids"] = new JArray(LayoutBoundaryProbeContract.CapabilityIds)
            };
        }

        private static JObject BuildG1Fixture(string root, bool semanticDrift)
        {
            string planPath = Path.Combine(root, "dimension_plan.json");
            string drawingPath = Path.Combine(root, "dimensioned.SLDDRW");
            string sidecarPath = Path.Combine(root, "dimension.verify.json");
            string qualificationPath = Path.Combine(root, "g0-qualification.json");
            string manifestPath = Path.Combine(root, "capabilities.json");
            var plan = new JObject
            {
                ["protocol_id"] = "solidworks-dimension-plan",
                ["schema_version"] = "1.0",
                ["plan_id"] = "DP-G1-CONTRACT",
                ["dimensions"] = new JArray(new JObject
                    { ["dimension_id"] = "D-G1-1" })
            };
            File.WriteAllText(planPath, plan.ToString());
            File.WriteAllBytes(drawingPath, new byte[] { 1, 3, 5, 7 });
            string planHash = FileSha256(planPath);
            string drawingHash = FileSha256(drawingPath);
            var dimension = new JObject
            {
                ["dimension_id"] = semanticDrift ? "D-G1-drift" : "D-G1-1",
                ["value_si"] = 0.05,
                ["model_persistent_references"] = new JArray("persistent-ref")
            };
            var sidecar = new JObject
            {
                ["protocol_id"] = "solidworks-dimension-drawing-verification",
                ["schema_version"] = "1.0",
                ["verified"] = true,
                ["plan_id"] = "DP-G1-CONTRACT",
                ["plan_file_path"] = planPath,
                ["plan_file_sha256"] = planHash,
                ["plan_canonical_sha256"] = CanonicalSha256(plan),
                ["output_path"] = drawingPath,
                ["artifact_sha256"] = drawingHash,
                ["in_memory_verification"] = new JObject
                    { ["verified"] = true, ["dimensions"] = new JArray(dimension) },
                ["reopen_verification"] = new JObject
                {
                    ["verified"] = true,
                    ["actual_total_count"] = 1,
                    ["dimensions"] = new JArray(dimension)
                }
            };
            File.WriteAllText(sidecarPath, sidecar.ToString());
            var qualification = new JObject
            {
                ["protocol_id"] = "solidworks-layout-g0-qualification",
                ["schema_version"] = "1.0",
                ["qualification_id"] = "G0-G1-CONTRACT",
                ["solidworks_revision"] = "33.5.0",
                ["overall_status"] = "complete"
            };
            File.WriteAllText(qualificationPath, qualification.ToString());
            var manifest = new JObject
            {
                ["protocol_id"] =
                    "solidworks-drawing-layout-executor-capabilities",
                ["schema_version"] = "1.0",
                ["registry_version"] = "1.0.0",
                ["solidworks_revision"] = "33.5.0",
                ["verification"] = "live_complete",
                ["capabilities"] = new JArray(new JObject
                    { ["id"] = "sheet_border_bounds", ["status"] = "supported" }),
                ["live_evidence"] = new JObject
                {
                    ["qualification_path"] = qualificationPath,
                    ["qualification_sha256"] = FileSha256(qualificationPath),
                    ["qualification_id"] = "G0-G1-CONTRACT",
                    ["solidworks_revision"] = "33.5.0"
                }
            };
            File.WriteAllText(manifestPath, manifest.ToString());
            return new JObject
            {
                ["protocol_id"] = LayoutPlanningHandoffContract.ProtocolId,
                ["schema_version"] = LayoutPlanningHandoffContract.SchemaVersion,
                ["source"] = new JObject
                {
                    ["dimension_plan"] = Artifact(planPath, planHash),
                    ["dimensioned_drawing"] = Artifact(drawingPath, drawingHash),
                    ["dimension_verification_sidecar"] = Artifact(sidecarPath,
                        FileSha256(sidecarPath))
                },
                ["boundary_capabilities"] = new JObject
                {
                    ["manifest"] = Artifact(manifestPath, FileSha256(manifestPath)),
                    ["qualification"] = Artifact(qualificationPath,
                        FileSha256(qualificationPath))
                },
                ["publication_directory"] = Path.Combine(root, "handoff-output"),
                ["minimum_spacing_m"] = new JObject
                {
                    ["object_to_object"] = 0.002,
                    ["object_to_frame"] = 0.005,
                    ["text_to_geometry"] = 0.001
                }
            };
        }

        private static string CanonicalSha256(JToken value)
        {
            JToken canonical = Canonicalize(value);
            using (var algorithm = System.Security.Cryptography.SHA256.Create())
                return String.Concat(algorithm.ComputeHash(
                    System.Text.Encoding.UTF8.GetBytes(canonical.ToString(
                        Newtonsoft.Json.Formatting.None)))
                    .Select(item => item.ToString("x2")));
        }

        private static JToken Canonicalize(JToken value)
        {
            if (value is JObject obj)
            {
                var result = new JObject();
                foreach (JProperty property in obj.Properties()
                    .OrderBy(item => item.Name, StringComparer.Ordinal))
                    result[property.Name] = Canonicalize(property.Value);
                return result;
            }
            if (value is JArray array)
                return new JArray(array.Select(Canonicalize));
            return value.DeepClone();
        }

        private static string FileSha256(string path)
        {
            using (var stream = File.OpenRead(path))
            using (var algorithm = System.Security.Cryptography.SHA256.Create())
                return String.Concat(algorithm.ComputeHash(stream)
                    .Select(value => value.ToString("x2")));
        }

        private static string Format(LayoutBoundaryProbeContractError error)
        {
            return error == null ? "<none>" : error.Code + " " +
                error.JsonPointer + " " + error.Message;
        }

        private static string Format(LayoutPlanningHandoffContractError error)
        {
            return error == null ? "<none>" : error.Code + " " +
                error.JsonPointer + " " + error.Message;
        }

        private static void Assert(bool value, string message)
        {
            if (!value) throw new InvalidOperationException(message);
        }

        private static void Pass(string name)
        {
            _passed++;
            Console.WriteLine("ok - " + name);
        }
    }
}
