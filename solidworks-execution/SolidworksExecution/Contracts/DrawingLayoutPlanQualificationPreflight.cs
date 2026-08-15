using System;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace SolidworksExecution.Contracts
{
    /// <summary>Bind one qualification-only native transaction to an immutable G7 case.</summary>
    public sealed class DrawingLayoutPlanQualificationPreflight
    {
        public bool TryValidate(DrawingLayoutExecutionPlan plan, string planPath,
            string planSha256, string outputPath, string matrixRequestPath,
            string matrixRequestSha256, string planningRequestSha256,
            string sourceDimensionRequestSha256, string caseId,
            out DrawingLayoutPlanContractError error)
        {
            error = null;
            if (plan == null || !IsHash(matrixRequestSha256) ||
                !IsHash(planningRequestSha256) || !IsHash(sourceDimensionRequestSha256) ||
                String.IsNullOrWhiteSpace(caseId))
                return Fail("DRAWING_LAYOUT_QUALIFICATION_REQUEST_INVALID", "",
                    "Plan, matrix/request hashes and case_id are required.", out error);
            string matrixPath;
            try
            {
                matrixPath = Path.GetFullPath(matrixRequestPath);
                if (!Path.IsPathRooted(matrixRequestPath) ||
                    !String.Equals(Path.GetExtension(matrixPath), ".json",
                        StringComparison.OrdinalIgnoreCase) || !File.Exists(matrixPath))
                    throw new InvalidDataException("matrix_request_path must be an existing JSON file.");
                if (!String.Equals(DrawingLayoutPlanContractValidator.FileSha256(matrixPath),
                    matrixRequestSha256, StringComparison.Ordinal))
                    return Fail("DRAWING_LAYOUT_G7_MATRIX_HASH_MISMATCH",
                        "/matrix_request_sha256", "The immutable G7 matrix hash changed.", out error);
            }
            catch (Exception ex)
            { return Fail("DRAWING_LAYOUT_G7_MATRIX_INVALID", "/matrix_request_path",
                ex.Message, out error); }

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
                    if (reader.Read()) throw new InvalidDataException("Matrix contains trailing JSON.");
                }
            }
            catch (Exception ex)
            { return Fail("DRAWING_LAYOUT_G7_MATRIX_INVALID", "/matrix_request_path",
                ex.Message, out error); }
            if (matrix.Value<string>("protocol_id") !=
                    "solidworks-drawing-layout-g7-matrix-request" ||
                matrix.Value<string>("schema_version") != "1.0" ||
                matrix.Value<string>("solidworks_revision") != "33.5.0")
                return Fail("DRAWING_LAYOUT_G7_MATRIX_INVALID", "/matrix_request_path",
                    "Matrix is not the frozen G7 contract for SolidWorks 33.5.0.", out error);
            JArray cases = matrix["positive_cases"] as JArray;
            JObject[] matches = cases == null ? new JObject[0] : cases.OfType<JObject>()
                .Where(item => item.Value<string>("case_id") == caseId).ToArray();
            if (matches.Length != 1)
                return Fail("DRAWING_LAYOUT_G7_CASE_MISSING", "/case_id",
                    "case_id must select exactly one positive G7 case.", out error);
            JObject row = matches[0];
            if (!PathEquals(row.Value<string>("plan_path"), planPath) ||
                row.Value<string>("plan_file_sha256") != planSha256 ||
                row.Value<string>("plan_canonical_sha256") != plan.PlanSha256 ||
                row.Value<string>("planning_request_sha256") != planningRequestSha256 ||
                row.Value<string>("source_dimension_request_sha256") !=
                    sourceDimensionRequestSha256 ||
                !PathEquals(row.Value<string>("output_path"), outputPath))
                return Fail("DRAWING_LAYOUT_G7_CASE_BINDING_MISMATCH", "/case_id",
                    "Plan, requests or output differ from the immutable G7 case.", out error);
            return true;
        }

        private static bool PathEquals(string left, string right)
        {
            if (String.IsNullOrWhiteSpace(left) || String.IsNullOrWhiteSpace(right)) return false;
            try { return String.Equals(Path.GetFullPath(left), Path.GetFullPath(right),
                StringComparison.OrdinalIgnoreCase); } catch { return false; }
        }
        private static bool IsHash(string value) => value != null && value.Length == 64 &&
            value.All(c => (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'));
        private static bool Fail(string code, string pointer, string message,
            out DrawingLayoutPlanContractError error)
        { error = new DrawingLayoutPlanContractError { Code = code, JsonPointer = pointer,
            Message = message }; return false; }
    }
}
