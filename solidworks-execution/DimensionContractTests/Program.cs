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

                Console.WriteLine("Dimension F0 contract tests passed: " + _passed + "/10" +
                    "; corpus requests: " + corpusRequests);
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

        private static JObject Artifact(string path, string hash)
        {
            return new JObject { ["path"] = path, ["sha256"] = hash };
        }

        private static string Format(DimensionApiProbeContractError error)
        {
            return error == null ? "<none>" : error.Code + " " + error.JsonPointer + " " + error.Message;
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
