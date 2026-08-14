using System;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace SolidworksExecution.Contracts
{
    /// <summary>
    /// Binds a qualification-only native transaction to one immutable F7 matrix case.  This is
    /// the bootstrap path for live evidence; it never changes the production capability registry.
    /// </summary>
    public sealed class DimensionPlanQualificationPreflight
    {
        public bool TryValidate(DimensionPlanExecutionPlan plan, string planPath,
            string planSha256, string outputPath, string matrixRequestPath,
            string matrixRequestSha256, string planningRequestSha256, string caseId,
            out string matrixPlanCanonicalSha256, out DimensionPlanContractError error)
        {
            matrixPlanCanonicalSha256 = null;
            error = null;
            if (plan == null) return Fail("DIMENSION_QUALIFICATION_REQUEST_INVALID", "/plan",
                "Compiled DimensionPlan is required.", out error);
            if (!IsHash(matrixRequestSha256) || !IsHash(planningRequestSha256))
                return Fail("DIMENSION_QUALIFICATION_REQUEST_INVALID", "",
                    "Matrix and planning-request SHA-256 values are required.", out error);
            if (String.IsNullOrWhiteSpace(caseId))
                return Fail("DIMENSION_QUALIFICATION_REQUEST_INVALID", "/case_id",
                    "case_id is required.", out error);
            string matrixPath;
            try
            {
                matrixPath = Path.GetFullPath(matrixRequestPath);
                if (!Path.IsPathRooted(matrixRequestPath) ||
                    !String.Equals(Path.GetExtension(matrixPath), ".json",
                        StringComparison.OrdinalIgnoreCase) || !File.Exists(matrixPath))
                    throw new InvalidDataException(
                        "matrix_request_path must be an existing absolute JSON file.");
                if (!String.Equals(DimensionPlanContractValidator.FileSha256(matrixPath),
                    matrixRequestSha256, StringComparison.Ordinal))
                    return Fail("DIMENSION_F7_MATRIX_HASH_MISMATCH", "/matrix_request_sha256",
                        "The immutable F7 matrix request hash changed.", out error);
            }
            catch (Exception exception)
            {
                return Fail("DIMENSION_F7_MATRIX_INVALID", "/matrix_request_path",
                    exception.Message, out error);
            }

            JObject matrix;
            try
            {
                using (var stream = File.OpenText(matrixPath))
                using (var reader = new JsonTextReader(stream)
                    { DateParseHandling = DateParseHandling.None })
                {
                    matrix = JObject.Load(reader, new JsonLoadSettings
                    {
                        DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error,
                        CommentHandling = CommentHandling.Ignore,
                        LineInfoHandling = LineInfoHandling.Ignore
                    });
                    if (reader.Read()) throw new InvalidDataException(
                        "Matrix request contains trailing JSON.");
                }
            }
            catch (Exception exception)
            {
                return Fail("DIMENSION_F7_MATRIX_INVALID", "/matrix_request_path",
                    exception.Message, out error);
            }
            if (matrix.Value<string>("protocol_id") !=
                    "solidworks-dimension-f7-matrix-request" ||
                matrix.Value<string>("schema_version") != "1.0" ||
                matrix.Value<string>("solidworks_revision") != "33.5.0")
                return Fail("DIMENSION_F7_MATRIX_INVALID", "/matrix_request_path",
                    "Matrix request is not the frozen F7 contract for SolidWorks 33.5.0.",
                    out error);
            JArray cases = matrix["cases"] as JArray;
            JObject[] matches = cases == null ? new JObject[0] : cases
                .OfType<JObject>()
                .Where(item => item.Value<string>("case_id") == caseId).ToArray();
            if (matches.Length != 1)
                return Fail("DIMENSION_F7_CASE_MISSING", "/case_id",
                    "case_id must select exactly one immutable F7 case.", out error);
            JObject row = matches[0];
            if (!PathEquals(row.Value<string>("plan_path"), planPath) ||
                row.Value<string>("plan_file_sha256") != planSha256 ||
                row.Value<string>("planning_request_sha256") != planningRequestSha256 ||
                !PathEquals(row.Value<string>("output_path"), outputPath))
                return Fail("DIMENSION_F7_CASE_BINDING_MISMATCH", "/case_id",
                    "Plan, request, or output differs from the immutable F7 matrix case.",
                    out error);
            string canonical = row.Value<string>("plan_canonical_sha256");
            if (!IsHash(canonical))
                return Fail("DIMENSION_F7_MATRIX_INVALID",
                    "/cases/plan_canonical_sha256",
                    "The selected F7 case lacks a canonical plan SHA-256.", out error);
            // Python owns the repository canonical-JSON contract used by the matrix and semantic
            // stage continuity. Json.NET formats some finite exponent values differently, so its
            // local contract hash is intentionally not compared across languages here. The caller
            // has already proven exact plan-file bytes and structural equality in the transaction
            // preflight; use the immutable matrix value for qualification sidecars/readback only.
            matrixPlanCanonicalSha256 = canonical;
            return true;
        }

        private static bool PathEquals(string left, string right)
        {
            if (String.IsNullOrWhiteSpace(left) || String.IsNullOrWhiteSpace(right))
                return false;
            try
            {
                return String.Equals(Path.GetFullPath(left), Path.GetFullPath(right),
                    StringComparison.OrdinalIgnoreCase);
            }
            catch { return false; }
        }

        private static bool IsHash(string value) => value != null && value.Length == 64 &&
            value.All(character => (character >= '0' && character <= '9') ||
                (character >= 'a' && character <= 'f'));

        private static bool Fail(string code, string pointer, string message,
            out DimensionPlanContractError error)
        {
            error = new DimensionPlanContractError
                { Code = code, JsonPointer = pointer, Message = message };
            return false;
        }
    }
}
