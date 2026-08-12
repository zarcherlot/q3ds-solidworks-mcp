using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using Newtonsoft.Json.Linq;
using SolidworksExecution.Models;

namespace SolidworksExecution.Services
{
    public sealed class HostBootstrapRunner
    {
        private const string ReportName = "host-preflight-report.json";
        private static readonly object RunGate = new object();

        public HostBootstrapResponse Run(HostBootstrapRequest request)
        {
            ValidateRequest(request);
            lock (RunGate)
            {
                return RunExclusive(request);
            }
        }

        private static HostBootstrapResponse RunExclusive(HostBootstrapRequest request)
        {
            string helperPath = Path.Combine(
                AppDomain.CurrentDomain.BaseDirectory,
                "HostBootstrap",
                "SolidWorksHostBootstrap.exe");
            if (!File.Exists(helperPath))
                throw new FileNotFoundException(
                    "The repository HostBootstrap helper is not deployed beside the execution service.",
                    helperPath);

            string outputDirectory = Path.GetFullPath(request.OutputDirectory);
            string reportPath = Path.Combine(outputDirectory, ReportName);
            string lockDirectory = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Q3DS",
                "SolidworksExecution");
            Directory.CreateDirectory(lockDirectory);
            string lockPath = Path.Combine(lockDirectory, "host-bootstrap.lock");

            var arguments = new List<string>
            {
                "--output-dir", outputDirectory,
                "--lock-file", lockPath,
                "--lock-timeout-seconds", "0",
                "--stale-lock-seconds", "7200",
                "--com-timeout-seconds", request.ComTimeoutSeconds.ToString(),
                "--regserver-timeout-seconds", request.RegserverTimeoutSeconds.ToString()
            };
            if (!String.IsNullOrWhiteSpace(request.DrawingTemplatePath))
            {
                arguments.Add("--template");
                arguments.Add(Path.GetFullPath(request.DrawingTemplatePath));
            }
            if (request.Visible) arguments.Add("--visible");
            if (request.KeepSolidworksRunning) arguments.Add("--keep-solidworks-running");

            switch (request.Mode.ToLowerInvariant())
            {
                case "inspect":
                    arguments.Add("--skip-solidworks-launch");
                    arguments.Add("--no-regserver");
                    break;
                case "verify":
                    arguments.Add("--execute");
                    arguments.Add("--no-regserver");
                    break;
                case "repair":
                    arguments.Add("--execute");
                    break;
            }

            var stdout = new StringBuilder();
            var stderr = new StringBuilder();
            var startInfo = new ProcessStartInfo(helperPath, JoinArguments(arguments))
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                WorkingDirectory = Path.GetDirectoryName(helperPath)
            };
            int timeoutSeconds = RunnerTimeoutSeconds(request);
            int exitCode;
            using (var process = new Process { StartInfo = startInfo })
            {
                process.OutputDataReceived += (sender, eventArgs) => AppendLine(stdout, eventArgs.Data);
                process.ErrorDataReceived += (sender, eventArgs) => AppendLine(stderr, eventArgs.Data);
                if (!process.Start())
                    throw new InvalidOperationException("Failed to start the repository HostBootstrap helper.");
                process.BeginOutputReadLine();
                process.BeginErrorReadLine();
                if (!process.WaitForExit(checked(timeoutSeconds * 1000)))
                {
                    try { process.Kill(); } catch { }
                    throw new TimeoutException(
                        "Repository HostBootstrap exceeded its bounded runner timeout of " +
                        timeoutSeconds + " seconds.");
                }
                process.WaitForExit();
                exitCode = process.ExitCode;
            }

            if (!File.Exists(reportPath))
                throw new InvalidDataException(
                    "HostBootstrap did not publish its required JSON report. stderr=" + Tail(stderr.ToString(), 2000));

            JObject report;
            byte[] reportBytes;
            try
            {
                reportBytes = File.ReadAllBytes(reportPath);
                report = JObject.Parse(Encoding.UTF8.GetString(reportBytes));
            }
            catch (Exception exception)
            {
                throw new InvalidDataException("HostBootstrap published malformed JSON.", exception);
            }
            string status = ValidateReport(report, exitCode);
            return new HostBootstrapResponse
            {
                Ok = status == "pass" || status == "warning",
                Status = status,
                Mode = request.Mode.ToLowerInvariant(),
                ReportPath = reportPath,
                ReportSha256 = Sha256(reportBytes),
                HelperSha256 = Sha256(helperPath),
                ExitCode = exitCode,
                Report = report,
                StdoutTail = Tail(stdout.ToString(), 4000),
                StderrTail = Tail(stderr.ToString(), 4000)
            };
        }

        private static void ValidateRequest(HostBootstrapRequest request)
        {
            if (request == null) throw new ArgumentException("A JSON request body is required.");
            string mode = (request.Mode ?? String.Empty).Trim().ToLowerInvariant();
            if (mode != "inspect" && mode != "verify" && mode != "repair")
                throw new ArgumentException("mode must be one of: inspect, verify, repair.");
            request.Mode = mode;
            ValidateDirectory(request.OutputDirectory);
            if (!String.IsNullOrWhiteSpace(request.DrawingTemplatePath))
            {
                ValidateSafePath(request.DrawingTemplatePath, "drawing_template_path");
                string template = Path.GetFullPath(request.DrawingTemplatePath);
                if (!Path.IsPathRooted(request.DrawingTemplatePath) ||
                    !String.Equals(Path.GetExtension(template), ".drwdot", StringComparison.OrdinalIgnoreCase))
                    throw new ArgumentException("drawing_template_path must be an absolute .DRWDOT path.");
                if (!File.Exists(template))
                    throw new ArgumentException("drawing_template_path does not exist.");
            }
            if (request.ComTimeoutSeconds < 10 || request.ComTimeoutSeconds > 600)
                throw new ArgumentException("com_timeout_seconds must be between 10 and 600.");
            if (request.RegserverTimeoutSeconds < 10 || request.RegserverTimeoutSeconds > 300)
                throw new ArgumentException("regserver_timeout_seconds must be between 10 and 300.");
            if (mode == "inspect" && (request.Visible || request.KeepSolidworksRunning))
                throw new ArgumentException(
                    "visible and keep_solidworks_running are invalid in inspect mode because launch is disabled.");
            if (mode == "repair" && !IsElevated())
                throw new ArgumentException(
                    "repair mode requires the already-running execution service process to be elevated; MCP cannot elevate it.");
        }

        private static void ValidateDirectory(string value)
        {
            ValidateSafePath(value, "output_directory");
            if (!Path.IsPathRooted(value))
                throw new ArgumentException("output_directory must be absolute.");
            string full = Path.GetFullPath(value);
            string root = Path.GetPathRoot(full);
            if (String.Equals(full.TrimEnd(Path.DirectorySeparatorChar), root.TrimEnd(Path.DirectorySeparatorChar), StringComparison.OrdinalIgnoreCase))
                throw new ArgumentException("output_directory must not be a filesystem root.");
            if (!Directory.Exists(full))
                throw new ArgumentException("output_directory must already exist.");
        }

        private static void ValidateSafePath(string value, string name)
        {
            if (String.IsNullOrWhiteSpace(value))
                throw new ArgumentException(name + " is required.");
            if (value.IndexOfAny(new[] { '*', '?', '[', ']', '"', '\r', '\n' }) >= 0)
                throw new ArgumentException(name + " contains unsupported characters.");
        }

        private static int RunnerTimeoutSeconds(HostBootstrapRequest request)
        {
            if (request.Mode == "inspect") return 60;
            if (request.Mode == "verify") return checked(request.ComTimeoutSeconds + 60);
            return checked((request.ComTimeoutSeconds * 2) + request.RegserverTimeoutSeconds + 60);
        }

        private static string ValidateReport(JObject report, int exitCode)
        {
            foreach (string name in new[]
            {
                "status", "host", "runtime", "python", "paths", "dependencies", "solidworks",
                "solidworks_installation", "template", "output_dir_check", "elevation",
                "bootstrap_policy", "actions", "temporary_files", "blocking_issues", "warnings",
                "generated_at"
            })
            {
                if (report[name] == null)
                    throw new InvalidDataException("HostBootstrap report is missing required field: " + name);
            }
            string status = (string)report["status"];
            if (status != "pass" && status != "warning" && status != "blocked")
                throw new InvalidDataException("HostBootstrap report has an invalid status.");
            if ((exitCode == 0) != (status == "pass" || status == "warning"))
                throw new InvalidDataException("HostBootstrap exit code and report status disagree.");
            if (exitCode != 0 && exitCode != 2)
                throw new InvalidDataException("HostBootstrap failed with fatal exit code " + exitCode + ".");
            return status;
        }

        private static bool IsElevated()
        {
            try
            {
                return new WindowsPrincipal(WindowsIdentity.GetCurrent())
                    .IsInRole(WindowsBuiltInRole.Administrator);
            }
            catch { return false; }
        }

        private static string JoinArguments(IEnumerable<string> arguments)
        {
            var result = new StringBuilder();
            foreach (string argument in arguments)
            {
                if (result.Length > 0) result.Append(' ');
                result.Append(Quote(argument));
            }
            return result.ToString();
        }

        private static string Quote(string value)
        {
            if (String.IsNullOrEmpty(value)) return "\"\"";
            if (value.IndexOfAny(new[] { ' ', '\t', '"' }) < 0) return value;
            var result = new StringBuilder("\"");
            int slashes = 0;
            foreach (char character in value)
            {
                if (character == '\\') { slashes++; continue; }
                if (character == '"')
                {
                    result.Append('\\', (slashes * 2) + 1);
                    result.Append('"');
                    slashes = 0;
                    continue;
                }
                result.Append('\\', slashes);
                slashes = 0;
                result.Append(character);
            }
            result.Append('\\', slashes * 2);
            result.Append('"');
            return result.ToString();
        }

        private static string Sha256(string path)
        {
            using (var stream = File.OpenRead(path))
            using (var algorithm = SHA256.Create())
            {
                return Hex(algorithm.ComputeHash(stream));
            }
        }

        private static string Sha256(byte[] value)
        {
            using (var algorithm = SHA256.Create())
            {
                return Hex(algorithm.ComputeHash(value));
            }
        }

        private static string Hex(byte[] hash)
        {
            var text = new StringBuilder(hash.Length * 2);
            foreach (byte value in hash) text.Append(value.ToString("x2"));
            return text.ToString();
        }

        private static void AppendLine(StringBuilder buffer, string value)
        {
            if (value == null) return;
            lock (buffer) buffer.AppendLine(value);
        }

        private static string Tail(string value, int length)
        {
            if (String.IsNullOrEmpty(value) || value.Length <= length) return value;
            return value.Substring(value.Length - length);
        }
    }
}
