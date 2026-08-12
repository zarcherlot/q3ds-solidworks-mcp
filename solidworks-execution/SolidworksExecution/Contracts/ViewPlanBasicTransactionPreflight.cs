using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;

namespace SolidworksExecution.Contracts
{
    /// <summary>
    /// COM-free B3 integrity and output-path gate. Every frozen artifact is re-hashed immediately
    /// before execution and both final paths must be new. This type is compiled into CI contract
    /// tests without SolidWorks Interop.
    /// </summary>
    public sealed class ViewPlanBasicTransactionPreflight
    {
        private static readonly string[] RequiredRoles =
        {
            "model",
            "drawing",
            "geometry_report",
            "readiness_report",
            "standard_view_image:front",
            "standard_view_image:back",
            "standard_view_image:left",
            "standard_view_image:right",
            "standard_view_image:top",
            "standard_view_image:bottom"
        };

        public bool TryValidate(ViewPlanBasicExecutionPlan plan, string requestedOutputPath,
            out ViewPlanBasicTransactionPaths paths, out ViewPlanExecutionContractError error)
        {
            paths = null;
            error = null;
            HashSet<string> normalizedInputs;
            if (!TryValidateFrozenInputs(plan, out normalizedInputs, out error))
                return false;

            string outputPath;
            try
            {
                if (string.IsNullOrWhiteSpace(requestedOutputPath) ||
                    !Path.IsPathRooted(requestedOutputPath))
                    throw new InvalidDataException("output_path must be absolute");
                outputPath = Path.GetFullPath(requestedOutputPath);
            }
            catch (Exception ex)
            {
                return Fail("VIEW_PLAN_OUTPUT_PATH_INVALID", "/output_path", ex.Message, out error);
            }
            if (!PathHasExtension(outputPath, ".SLDDRW"))
                return Fail("VIEW_PLAN_OUTPUT_PATH_INVALID", "/output_path",
                    "output_path must end with .SLDDRW.", out error);
            string directory = Path.GetDirectoryName(outputPath);
            if (string.IsNullOrEmpty(directory) || !Directory.Exists(directory))
                return Fail("VIEW_PLAN_OUTPUT_DIRECTORY_NOT_FOUND", "/output_path",
                    "The output directory must already exist.", out error);
            if (normalizedInputs.Contains(outputPath))
                return Fail("VIEW_PLAN_OUTPUT_PATH_INVALID", "/output_path",
                    "output_path must differ from every frozen input artifact.", out error);
            string reportPath = outputPath + ".verification.json";
            if (File.Exists(outputPath) || File.Exists(reportPath))
                return Fail("VIEW_PLAN_OUTPUT_EXISTS", "/output_path",
                    "Neither output_path nor its verification report may already exist.", out error);

            paths = new ViewPlanBasicTransactionPaths
            {
                OutputPath = outputPath,
                ReportPath = reportPath
            };
            return true;
        }

        public bool TryValidateFrozenInputs(ViewPlanBasicExecutionPlan plan,
            out ViewPlanExecutionContractError error)
        {
            HashSet<string> ignored;
            return TryValidateFrozenInputs(plan, out ignored, out error);
        }

        private static bool TryValidateFrozenInputs(ViewPlanBasicExecutionPlan plan,
            out HashSet<string> normalizedInputs, out ViewPlanExecutionContractError error)
        {
            normalizedInputs = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            error = null;
            if (plan == null || plan.InputArtifacts == null || plan.InputArtifacts.Count != 10)
                return Fail("VIEW_PLAN_INPUT_BINDING_INVALID", "",
                    "The execution plan must bind exactly ten source artifacts.", out error);
            var roles = new HashSet<string>(StringComparer.Ordinal);
            var normalizedByRole = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (ViewPlanBoundArtifact artifact in plan.InputArtifacts)
            {
                if (artifact == null || string.IsNullOrEmpty(artifact.Role) ||
                    !roles.Add(artifact.Role))
                    return Fail("VIEW_PLAN_INPUT_BINDING_INVALID", "/input_artifacts",
                        "Input artifact roles must be present and unique.", out error);
                string fullPath;
                try
                {
                    if (string.IsNullOrWhiteSpace(artifact.Path) ||
                        !Path.IsPathRooted(artifact.Path))
                        throw new InvalidDataException("path must be absolute");
                    fullPath = Path.GetFullPath(artifact.Path);
                }
                catch (Exception ex)
                {
                    return Fail("VIEW_PLAN_INPUT_PATH_INVALID", "/input_artifacts",
                        artifact.Role + ": " + ex.Message, out error);
                }
                if (!File.Exists(fullPath))
                    return Fail("VIEW_PLAN_INPUT_NOT_FOUND", "/input_artifacts",
                        artifact.Role + ": file does not exist: " + fullPath, out error);
                string actual = ComputeFileSha256(fullPath);
                if (!string.Equals(actual, artifact.Sha256, StringComparison.OrdinalIgnoreCase))
                    return Fail("VIEW_PLAN_INPUT_HASH_MISMATCH", "/input_artifacts",
                        artifact.Role + ": SHA-256 differs from the frozen plan.", out error);
                if (!normalizedInputs.Add(fullPath))
                    return Fail("VIEW_PLAN_INPUT_BINDING_INVALID", "/input_artifacts",
                        "Each input artifact role must bind a distinct absolute path.", out error);
                normalizedByRole.Add(artifact.Role, fullPath);
            }
            foreach (string requiredRole in RequiredRoles)
                if (!roles.Contains(requiredRole))
                    return Fail("VIEW_PLAN_INPUT_BINDING_INVALID", "/input_artifacts",
                        "Missing required input artifact role: " + requiredRole + ".", out error);
            if (!normalizedByRole.TryGetValue("model", out string boundModel) ||
                !PathEquals(boundModel, plan.ModelPath) || !PathHasExtension(boundModel, ".SLDPRT") ||
                !HashEquals(ArtifactHash(plan, "model"), plan.ModelSha256))
                return Fail("VIEW_PLAN_INPUT_BINDING_INVALID", "/model_path",
                    "The model artifact must exactly bind model_path/model_sha256 and use .SLDPRT.",
                    out error);
            if (!normalizedByRole.TryGetValue("drawing", out string boundDrawing) ||
                !PathEquals(boundDrawing, plan.DrawingPath) ||
                !PathHasExtension(boundDrawing, ".SLDDRW") ||
                !HashEquals(ArtifactHash(plan, "drawing"), plan.DrawingSha256))
                return Fail("VIEW_PLAN_INPUT_BINDING_INVALID", "/drawing_path",
                    "The drawing artifact must exactly bind drawing_path/drawing_sha256 and use .SLDDRW.",
                    out error);
            return true;
        }

        private static string ComputeFileSha256(string path)
        {
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read,
                FileShare.Read))
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "")
                    .ToLowerInvariant();
        }

        private static bool PathEquals(string first, string second)
        {
            try { return string.Equals(Path.GetFullPath(first), Path.GetFullPath(second),
                StringComparison.OrdinalIgnoreCase); }
            catch { return false; }
        }

        private static string ArtifactHash(ViewPlanBasicExecutionPlan plan, string role)
        {
            ViewPlanBoundArtifact artifact = plan.InputArtifacts.FirstOrDefault(item =>
                item != null && string.Equals(item.Role, role, StringComparison.Ordinal));
            return artifact != null ? artifact.Sha256 : null;
        }

        private static bool HashEquals(string first, string second)
        {
            return !string.IsNullOrWhiteSpace(first) &&
                string.Equals(first, second, StringComparison.OrdinalIgnoreCase);
        }

        private static bool PathHasExtension(string path, string extension)
        {
            try { return string.Equals(Path.GetExtension(path), extension,
                StringComparison.OrdinalIgnoreCase); }
            catch { return false; }
        }

        private static bool Fail(string code, string pointer, string message,
            out ViewPlanExecutionContractError error)
        {
            error = new ViewPlanExecutionContractError
            {
                Code = code,
                JsonPointer = pointer,
                Message = message
            };
            return false;
        }
    }

    public sealed class ViewPlanBasicTransactionPaths
    {
        public string OutputPath { get; internal set; }
        public string ReportPath { get; internal set; }
    }
}
