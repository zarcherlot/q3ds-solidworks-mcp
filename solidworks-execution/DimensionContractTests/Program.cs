using System;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using SolidworksExecution.Contracts;

namespace DimensionContractTests
{
    internal static class Program
    {
        private static int _passed;

        private static int Main(string[] args)
        {
            try
            {
                var validator = new DimensionApiProbeContract();
                var valid = BuildValid();
                DimensionApiProbeRequest request;
                DimensionApiProbeContractError error;
                Assert(validator.TryParse(valid, out request, out error), Format(error));
                Assert(request.CapabilityIds.Length == 14, "catalog length");
                Pass("valid frozen F0 probe request");

                var research = BuildResearchPair();
                Assert(validator.TryParse(research, out request, out error), Format(error));
                Assert(request.SourceKind == "research_model_drawing_pair" &&
                    request.SourceModel != null && request.SourceDrawing != null,
                    "research pair was not preserved");
                Pass("valid research model/drawing pair request");

                var mismatchedPair = BuildResearchPair();
                mismatchedPair["source"]["source_drawing"]["path"] =
                    Path.Combine(Path.GetTempPath(), "dimension-f0-research", "other.SLDDRW");
                Assert(!validator.TryParse(mismatchedPair, out request, out error) &&
                    error.JsonPointer == "/source", "mismatched research basename accepted");
                Pass("mismatched research basename rejected");

                var splitPair = BuildResearchPair();
                splitPair["source"]["source_drawing"]["path"] =
                    Path.Combine(Path.GetTempPath(), "dimension-f0-other", "part.SLDDRW");
                Assert(!validator.TryParse(splitPair, out request, out error) &&
                    error.JsonPointer == "/source", "split-directory research pair accepted");
                Pass("split-directory research pair rejected");

                var unknown = (JObject)valid.DeepClone();
                unknown["legacy_tool"] = "auto_dimension_drawing";
                Assert(!validator.TryParse(unknown, out request, out error) &&
                    error.JsonPointer == "/legacy_tool", "unknown field must fail closed");
                Pass("legacy executor field rejected");

                var wrongRevision = (JObject)valid.DeepClone();
                wrongRevision["required_solidworks_revision"] = "34.0.0";
                Assert(!validator.TryParse(wrongRevision, out request, out error),
                    "wrong revision accepted");
                Pass("non-SP5 revision rejected");

                var reordered = (JObject)valid.DeepClone();
                var ids = (JArray)reordered["capability_ids"];
                JToken first = ids[0];
                ids[0] = ids[1];
                ids[1] = first;
                Assert(!validator.TryParse(reordered, out request, out error),
                    "reordered catalog accepted");
                Pass("capability catalog drift rejected");

                var relative = (JObject)valid.DeepClone();
                relative["source"]["verified_drawing"]["path"] = "drawing.SLDDRW";
                Assert(!validator.TryParse(relative, out request, out error),
                    "relative upstream path accepted");
                Pass("relative upstream path rejected");

                var badHash = (JObject)valid.DeepClone();
                badHash["source"]["view_plan"]["sha256"] = new string('A', 64);
                Assert(!validator.TryParse(badHash, out request, out error),
                    "non-canonical hash accepted");
                Pass("non-canonical SHA-256 rejected");

                var collision = (JObject)valid.DeepClone();
                collision["publication_directory"] =
                    Path.GetDirectoryName(collision["source"]["verified_drawing"]["path"].Value<string>());
                Assert(!validator.TryParse(collision, out request, out error),
                    "upstream publication collision accepted");
                Pass("upstream publication collision rejected");

                var handoffValidator = new DimensionPlanningHandoffContract();
                DimensionPlanningHandoffRequest handoffRequest;
                DimensionPlanningHandoffContractError handoffError;
                var validHandoff = BuildValidHandoff();
                Assert(handoffValidator.TryParse(validHandoff, out handoffRequest,
                    out handoffError), Format(handoffError));
                Assert(handoffRequest.ApprovedUserInputs.Count == 1,
                    "approved input was not preserved");
                Pass("valid F1 handoff request");

                var legacyHandoff = (JObject)validHandoff.DeepClone();
                legacyHandoff["legacy_tool"] = "auto_dimension_drawing";
                Assert(!handoffValidator.TryParse(legacyHandoff, out handoffRequest,
                    out handoffError) && handoffError.JsonPointer == "/legacy_tool",
                    "legacy F1 field accepted");
                Pass("F1 legacy field rejected");

                var unapproved = (JObject)validHandoff.DeepClone();
                unapproved["approved_user_inputs"][0]["source_tier"] =
                    "reference_geometry_measurement";
                Assert(!handoffValidator.TryParse(unapproved, out handoffRequest,
                    out handoffError) && handoffError.JsonPointer.EndsWith("/source_tier"),
                    "unapproved source tier accepted");
                Pass("F1 source provenance enforced");

                var duplicateInput = (JObject)validHandoff.DeepClone();
                ((JArray)duplicateInput["approved_user_inputs"]).Add(
                    duplicateInput["approved_user_inputs"][0].DeepClone());
                Assert(!handoffValidator.TryParse(duplicateInput, out handoffRequest,
                    out handoffError) && handoffError.JsonPointer.EndsWith("/input_id"),
                    "duplicate approved input ID accepted");
                Pass("F1 approved input IDs unique");

                var invalidValue = (JObject)validHandoff.DeepClone();
                invalidValue["approved_user_inputs"][0]["value"]["quantity_kind"] =
                    "pixel";
                Assert(!handoffValidator.TryParse(invalidValue, out handoffRequest,
                    out handoffError), "pixel-derived input accepted");
                Pass("F1 pixel-derived values rejected");

                var validationOutput = (JObject)validHandoff.DeepClone();
                validationOutput["publication_directory"] = Path.Combine(
                    Path.GetTempPath(), "validation", "f1-output");
                Assert(!handoffValidator.TryParse(validationOutput,
                    out handoffRequest, out handoffError) &&
                    handoffError.JsonPointer == "/publication_directory",
                    "validation descendant output accepted");
                Pass("F1 validation tree remains read-only");

                var invalidApprovalTime = (JObject)validHandoff.DeepClone();
                invalidApprovalTime["approved_user_inputs"][0]["approved_at_utc"] =
                    "2026-08-13";
                Assert(!handoffValidator.TryParse(invalidApprovalTime,
                    out handoffRequest, out handoffError) &&
                    handoffError.JsonPointer.EndsWith("/approved_at_utc"),
                    "non-RFC3339 approval time accepted");
                Pass("F1 approval time is strict RFC 3339");

                int f4Contracts = DimensionPlanF4ContractTests.Run(
                    Environment.GetEnvironmentVariable("DIMENSION_PLAN_SCHEMA_PATH"),
                    Environment.GetEnvironmentVariable("DIMENSION_CAPABILITY_REGISTRY_PATH"));
                int f5Contracts = DimensionPlanF5ContractTests.Run(
                    Environment.GetEnvironmentVariable("DIMENSION_PLAN_SCHEMA_PATH"),
                    Environment.GetEnvironmentVariable("DIMENSION_CAPABILITY_REGISTRY_PATH"));

                int corpusRequests = 0;
                if (args.Length == 1)
                {
                    string directory = Path.GetFullPath(args[0]);
                    Assert(Directory.Exists(directory),
                        "probe request directory does not exist: " + directory);
                    foreach (string path in Directory.GetFiles(directory, "*.json")
                        .OrderBy(value => value, StringComparer.OrdinalIgnoreCase))
                    {
                        var candidate = JObject.Parse(File.ReadAllText(path));
                        Assert(validator.TryParse(candidate, out request, out error),
                            Path.GetFileName(path) + ": " + Format(error));
                        Assert(validator.TryPreflight(request, out error),
                            Path.GetFileName(path) + ": " + Format(error));
                        corpusRequests++;
                        Console.WriteLine("ok - corpus request " + Path.GetFileName(path));
                    }
                    Assert(corpusRequests > 0, "probe request directory is empty");
                }
                else if (args.Length > 1)
                {
                    throw new InvalidOperationException(
                        "usage: DimensionContractTests.exe [probe-request-directory]");
                }

                Console.WriteLine("Dimension F0-F7 contract tests passed: " + _passed + "/17" +
                    "; F4/F7: " + f4Contracts + "/8; F5: " + f5Contracts +
                    "/10; corpus requests: " + corpusRequests);
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine(ex);
                return 1;
            }
        }

        private static JObject BuildValid()
        {
            string root = Path.Combine(Path.GetTempPath(), "dimension-f0-contract");
            string hash = new string('a', 64);
            return new JObject
            {
                ["protocol_id"] = DimensionApiProbeContract.ProtocolId,
                ["schema_version"] = DimensionApiProbeContract.SchemaVersion,
                ["source"] = new JObject
                {
                    ["kind"] = "frozen_viewplan_drawing",
                    ["view_plan"] = Artifact(Path.Combine(root, "view-plan.json"), hash),
                    ["verified_drawing"] = Artifact(Path.Combine(root, "viewed.SLDDRW"), hash),
                    ["verification_sidecar"] = Artifact(Path.Combine(root, "viewed.verify.json"), hash)
                },
                ["publication_directory"] = Path.Combine(root, "probe-output"),
                ["required_solidworks_revision"] =
                    DimensionApiProbeContract.RequiredSolidWorksRevision,
                ["capability_ids"] = new JArray(DimensionApiProbeContract.CapabilityIds)
            };
        }

        private static JObject BuildResearchPair()
        {
            string root = Path.Combine(Path.GetTempPath(), "dimension-f0-research");
            string hash = new string('b', 64);
            return new JObject
            {
                ["protocol_id"] = DimensionApiProbeContract.ProtocolId,
                ["schema_version"] = DimensionApiProbeContract.SchemaVersion,
                ["source"] = new JObject
                {
                    ["kind"] = "research_model_drawing_pair",
                    ["source_model"] = Artifact(Path.Combine(root, "part.SLDPRT"), hash),
                    ["source_drawing"] = Artifact(Path.Combine(root, "part.SLDDRW"), hash),
                    ["drawing_template"] = Artifact(Path.Combine(root, "A3.DRWDOT"), hash)
                },
                ["publication_directory"] = Path.Combine(root, "probe-output"),
                ["required_solidworks_revision"] =
                    DimensionApiProbeContract.RequiredSolidWorksRevision,
                ["capability_ids"] = new JArray(DimensionApiProbeContract.CapabilityIds)
            };
        }

        private static JObject BuildValidHandoff()
        {
            string root = Path.Combine(Path.GetTempPath(), "dimension-f1-contract");
            string hash = new string('c', 64);
            return new JObject
            {
                ["protocol_id"] = DimensionPlanningHandoffContract.ProtocolId,
                ["schema_version"] = DimensionPlanningHandoffContract.SchemaVersion,
                ["source"] = new JObject
                {
                    ["view_plan"] = Artifact(Path.Combine(root, "view-plan.json"), hash),
                    ["verified_drawing"] = Artifact(
                        Path.Combine(root, "viewed.SLDDRW"), hash),
                    ["verification_sidecar"] = Artifact(
                        Path.Combine(root, "viewed.verify.json"), hash)
                },
                ["publication_directory"] = Path.Combine(root, "handoff-output"),
                ["approved_user_inputs"] = new JArray(new JObject
                {
                    ["input_id"] = "approved-length-1",
                    ["source_tier"] = "user_confirmed_input",
                    ["approved_by"] = "contract-test",
                    ["approved_at_utc"] = "2026-08-13T00:00:00Z",
                    ["approval_reference"] = "contract fixture",
                    ["target_feature_ids"] = new JArray("Boss-Extrude1"),
                    ["value"] = new JObject
                    {
                        ["kind"] = "quantity",
                        ["quantity_kind"] = "length",
                        ["value_si"] = 0.01
                    }
                })
            };
        }

        private static JObject Artifact(string path, string hash)
        {
            return new JObject { ["path"] = path, ["sha256"] = hash };
        }

        private static string Format(DimensionApiProbeContractError error)
        {
            return error == null ? "<none>" : error.Code + " " + error.JsonPointer + " " + error.Message;
        }

        private static string Format(DimensionPlanningHandoffContractError error)
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
