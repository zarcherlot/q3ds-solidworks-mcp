using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace SolidworksExecution.Contracts
{
    /// <summary>COM-free, hash-locked validator for DimensionPlan 1.0.</summary>
    public sealed class DimensionPlanContractValidator
    {
        public const string ProtocolId = "solidworks-dimension-plan";
        public const string SchemaVersion = "1.0";
        public const string ContractSha256 =
            "3b28b65cddadf3e1cb279ee786b42edb29a2e1867b3d4dd7bf182e20438e19af";

        private readonly ViewPlanContractValidator _validator;

        public DimensionPlanContractValidator(string schemaPath)
        {
            _validator = new ViewPlanContractValidator(schemaPath, ContractSha256,
                "DimensionPlan", "DIMENSION_PLAN_SCHEMA_INVALID");
        }

        public bool TryParse(JToken candidate, out DimensionPlanDocument document,
            out DimensionPlanContractError error)
        {
            document = null;
            error = null;
            ViewPlanContractError validationError;
            if (!_validator.TryValidate(candidate, out validationError))
            {
                error = FromViewPlanError(validationError);
                return false;
            }

            var root = (JObject)candidate;
            var canonical = (JObject)Canonicalize(root);
            document = new DimensionPlanDocument
            {
                ProtocolId = root.Value<string>("protocol_id"),
                SchemaVersion = root.Value<string>("schema_version"),
                PlanId = root.Value<string>("plan_id"),
                CanonicalPlan = canonical,
                CanonicalSha256 = Sha256(Encoding.UTF8.GetBytes(
                    canonical.ToString(Formatting.None)))
            };
            return true;
        }

        private static DimensionPlanContractError FromViewPlanError(ViewPlanContractError error)
        {
            return new DimensionPlanContractError
            {
                Code = error != null ? error.Code : "DIMENSION_PLAN_SCHEMA_INVALID",
                JsonPointer = error != null ? error.JsonPointer : "",
                Message = error != null ? error.Message : "DimensionPlan validation failed."
            };
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
            if (array != null)
                return new JArray(array.Select(Canonicalize));
            return token.DeepClone();
        }

        internal static string FileSha256(string path)
        {
            return Sha256(File.ReadAllBytes(path));
        }

        internal static string Sha256(byte[] payload)
        {
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(payload)).Replace("-", "")
                    .ToLowerInvariant();
        }
    }

    public sealed class DimensionPlanDocument
    {
        public string ProtocolId { get; internal set; }
        public string SchemaVersion { get; internal set; }
        public string PlanId { get; internal set; }
        public JObject CanonicalPlan { get; internal set; }
        public string CanonicalSha256 { get; internal set; }
    }

    public sealed class DimensionPlanContractError
    {
        public string Code { get; internal set; }
        public string JsonPointer { get; internal set; }
        public string Message { get; internal set; }
    }
}
