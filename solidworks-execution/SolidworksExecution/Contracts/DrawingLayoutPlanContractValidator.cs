using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace SolidworksExecution.Contracts
{
    /// <summary>COM-free, hash-locked validator for DrawingLayoutPlan 1.0.</summary>
    public sealed class DrawingLayoutPlanContractValidator
    {
        public const string ProtocolId = "solidworks-drawing-layout-plan";
        public const string SchemaVersion = "1.0";
        public const string ContractSha256 =
            "ef8b18ab2f1672b0ee7edab203909b5b3150b52b1609ae1ed84fd17939624747";

        private readonly ViewPlanContractValidator _validator;

        public DrawingLayoutPlanContractValidator(string schemaPath)
        {
            _validator = new ViewPlanContractValidator(schemaPath, ContractSha256,
                "DrawingLayoutPlan", "DRAWING_LAYOUT_PLAN_SCHEMA_INVALID");
        }

        public bool TryParse(JToken candidate, out DrawingLayoutPlanDocument document,
            out DrawingLayoutPlanContractError error)
        {
            document = null; error = null;
            ViewPlanContractError validationError;
            if (!_validator.TryValidate(candidate, out validationError))
            {
                error = new DrawingLayoutPlanContractError
                {
                    Code = validationError != null ? validationError.Code :
                        "DRAWING_LAYOUT_PLAN_SCHEMA_INVALID",
                    JsonPointer = validationError != null ? validationError.JsonPointer : "",
                    Message = validationError != null ? validationError.Message :
                        "DrawingLayoutPlan validation failed."
                };
                return false;
            }
            var root = (JObject)candidate;
            var canonical = (JObject)Canonicalize(root);
            document = new DrawingLayoutPlanDocument
            {
                PlanId = root.Value<string>("plan_id"), CanonicalPlan = canonical,
                CanonicalSha256 = DimensionPlanningHandoffContract.CanonicalSha256(root)
            };
            return true;
        }

        internal static JToken Canonicalize(JToken token)
        {
            var obj = token as JObject;
            if (obj != null)
            {
                var sorted = new JObject();
                foreach (JProperty property in obj.Properties().OrderBy(
                    item => item.Name, StringComparer.Ordinal))
                    sorted[property.Name] = Canonicalize(property.Value);
                return sorted;
            }
            var array = token as JArray;
            return array != null ? new JArray(array.Select(Canonicalize)) : token.DeepClone();
        }

        internal static string FileSha256(string path) => Sha256(File.ReadAllBytes(path));
        internal static string Sha256(byte[] bytes)
        {
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "")
                    .ToLowerInvariant();
        }
    }

    public sealed class DrawingLayoutPlanDocument
    {
        public string PlanId { get; internal set; }
        public JObject CanonicalPlan { get; internal set; }
        public string CanonicalSha256 { get; internal set; }
    }

    public sealed class DrawingLayoutPlanContractError
    {
        public string Code { get; internal set; }
        public string JsonPointer { get; internal set; }
        public string Message { get; internal set; }
    }
}
