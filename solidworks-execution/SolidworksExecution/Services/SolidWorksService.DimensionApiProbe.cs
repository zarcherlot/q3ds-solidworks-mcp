using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using Newtonsoft.Json.Linq;
using SolidWorks.Interop.sldworks;
using SolidworksExecution.Contracts;
using SolidworksExecution.Models;

namespace SolidworksExecution.Services
{
    public partial class SolidWorksService
    {
        private static readonly object SessionOwnershipGate = new object();
        private static int? _ownedSolidWorksProcessId;
        private bool _managedSemanticTask;
        private bool _managedConnectionAttempted;
        private bool _managedSessionOwned;
        private JObject _managedLifecycle;
        private static readonly string[] ManagedSolidWorksProcessNames =
        {
            "SLDWORKS", "sldProcMon", "swCefSubProc"
        };

        public JObject RunManagedDimensionApiProbe(DimensionApiProbeRequest request,
            JObject sourceRequest)
        {
            Dictionary<string, object> readiness = EnsureReady();
            bool attached = readiness.ContainsKey("comAttached") &&
                Convert.ToBoolean(readiness["comAttached"]);
            int ownedProcessId;
            bool ownsSession = TryGetOwnedSolidWorksProcessId(out ownedProcessId);
            var lifecycle = new JObject
            {
                ["ensure_ready"] = JObject.FromObject(readiness),
                ["session_owned_by_execution_service"] = ownsSession,
                ["owned_process_id"] = ownsSession ? (JToken)ownedProcessId : JValue.CreateNull()
            };

            if (!attached)
            {
                lifecycle["cleanup"] = ownsSession
                    ? CleanupOwnedSolidWorksSession()
                    : SkippedCleanup("SolidWorks was not attached and no owned session was recorded.");
                return new JObject
                {
                    ["status"] = "blocked",
                    ["error"] = new JObject
                    {
                        ["code"] = "COM_ATTACH_FAILED",
                        ["message"] = readiness.ContainsKey("launchError")
                            ? Convert.ToString(readiness["launchError"])
                            : "SolidWorks could not be attached or launched by the Execution Service."
                    },
                    ["lifecycle"] = lifecycle
                };
            }

            JObject result = null;
            JObject cleanup = null;
            try
            {
                result = RunDimensionApiProbe(request, sourceRequest);
            }
            finally
            {
                cleanup = ownsSession
                    ? CleanupOwnedSolidWorksSession()
                    : SkippedCleanup("A pre-existing session was preserved.");
            }

            lifecycle["cleanup"] = cleanup;
            result["lifecycle"] = lifecycle;
            if (ownsSession && !String.Equals(cleanup.Value<string>("status"), "pass",
                    StringComparison.Ordinal))
            {
                result["status"] = "cleanup_blocked";
                result["cleanup_failure"] = cleanup.DeepClone();
            }
            return result;
        }

        public JObject RunDimensionApiProbe(DimensionApiProbeRequest request,
            JObject sourceRequest)
        {
            if (!EnsureConnected())
                return new JObject
                {
                    ["status"] = "blocked",
                    ["error"] = new JObject
                    {
                        ["code"] = "COM_ATTACH_FAILED",
                        ["message"] =
                            "SolidWorks process not found or COM not registered."
                    }
                };

            var executor = new DimensionApiProbeExecutor(_solidWorks);
            return executor.Execute(request, sourceRequest);
        }

        public JObject CleanupExplicitIdleSolidWorksSession(int expectedProcessId,
            bool allowUnownedIdleSession, IEnumerable<string> expectedOpenDocumentPaths)
        {
            if (!allowUnownedIdleSession)
                return BlockedCleanup("EXPLICIT_CLEANUP_NOT_AUTHORIZED",
                    "allow_unowned_idle_session=true is required.");
            if (expectedProcessId <= 0)
                return BlockedCleanup("EXPECTED_PROCESS_ID_REQUIRED",
                    "A positive expected_process_id is required.");
            return CleanupSolidWorksSession(expectedProcessId, true,
                "explicit_idle_session_cleanup", expectedOpenDocumentPaths);
        }

        public JObject CleanupOwnedSolidWorksSession()
        {
            int ownedProcessId;
            if (!TryGetOwnedSolidWorksProcessId(out ownedProcessId))
                return SkippedCleanup("No live Execution Service-owned session was recorded.");
            return CleanupSolidWorksSession(ownedProcessId, true,
                "execution_service_owned_session");
        }

        private JObject CleanupSolidWorksSession(int expectedProcessId, bool authorized,
            string ownership, IEnumerable<string> expectedOpenDocumentPaths = null)
        {
            if (!authorized)
                return BlockedCleanup("SESSION_NOT_OWNED",
                    "The Execution Service does not own this SolidWorks session.");

            HashSet<int> solidWorksPids = SnapshotSolidWorksProcessIds();
            if (solidWorksPids.Count == 0)
            {
                HashSet<int> orphanedChildren = SnapshotManagedSolidWorksProcessIds();
                if (orphanedChildren.Count != 0)
                    return BlockedCleanup("SOLIDWORKS_CHILD_PROCESSES_REMAIN",
                        "SolidWorks is absent but related child processes remain.",
                        orphanedChildren);
                ClearOwnedSolidWorksSession(expectedProcessId);
                _solidWorks = null;
                IsConnected = false;
                return new JObject
                {
                    ["status"] = "pass",
                    ["ownership"] = ownership,
                    ["expected_process_id"] = expectedProcessId,
                    ["already_exited"] = true,
                    ["remaining_process_ids"] = new JArray()
                };
            }
            if (solidWorksPids.Count != 1 || !solidWorksPids.Contains(expectedProcessId))
                return BlockedCleanup("SOLIDWORKS_PROCESS_ID_MISMATCH",
                    "Cleanup requires exactly one SolidWorks process matching expected_process_id.",
                    solidWorksPids);

            bool cleanupAttached = false;
            DateTime attachDeadline = DateTime.UtcNow.AddSeconds(5);
            do
            {
                cleanupAttached = EnsureConnected();
                if (cleanupAttached) break;
                Thread.Sleep(250);
            }
            while (DateTime.UtcNow < attachDeadline);

            if (!cleanupAttached)
            {
                // A persisted ownership lease can outlive the ROT entry during SolidWorks startup
                // or shutdown.  Never extend this fallback to an unowned/explicit-recovery
                // session: without COM we cannot inventory its open documents.  For the exact
                // Execution Service-owned PID, however, failing closed must still leave no task
                // process behind, so terminate only the already validated main PID and the
                // bounded set of SolidWorks helper processes observed with it.
                if (!String.Equals(ownership, "execution_service_owned_session",
                        StringComparison.Ordinal))
                    return BlockedCleanup("COM_ATTACH_FAILED",
                        "The exact SolidWorks process exists but COM attachment failed.",
                        solidWorksPids);

                HashSet<int> trackedWithoutCom = SnapshotManagedSolidWorksProcessIds();
                JArray terminationErrors = ForceTerminateTrackedProcesses(
                    expectedProcessId, trackedWithoutCom);
                DateTime exitDeadline = DateTime.UtcNow.AddSeconds(15);
                HashSet<int> remainingWithoutCom;
                do
                {
                    remainingWithoutCom = SnapshotManagedSolidWorksProcessIds();
                    if (remainingWithoutCom.Count == 0) break;
                    Thread.Sleep(250);
                }
                while (DateTime.UtcNow < exitDeadline);

                if (remainingWithoutCom.Count != 0)
                    return BlockedCleanup("SOLIDWORKS_PROCESSES_REMAIN",
                        "The owned SolidWorks session could not be attached or terminated within the bounded cleanup timeout.",
                        remainingWithoutCom);

                _solidWorks = null;
                IsConnected = false;
                ClearOwnedSolidWorksSession(expectedProcessId);
                return new JObject
                {
                    ["status"] = "pass",
                    ["ownership"] = ownership,
                    ["expected_process_id"] = expectedProcessId,
                    ["com_attach_failed"] = true,
                    ["exit_app_invoked"] = false,
                    ["force_termination_used"] = true,
                    ["force_termination_errors"] = terminationErrors,
                    ["tracked_process_ids"] = new JArray(
                        trackedWithoutCom.OrderBy(value => value)),
                    ["remaining_process_ids"] = new JArray()
                };
            }

            var openDocuments = new List<IModelDoc2>();
            try
            {
                Array documents = _solidWorks.GetDocuments() as Array;
                if (documents != null)
                    foreach (object candidate in documents)
                    {
                        IModelDoc2 document = candidate as IModelDoc2;
                        if (document != null) openDocuments.Add(document);
                    }
                IModelDoc2 active = _solidWorks.IActiveDoc2 as IModelDoc2;
                if (active != null && !openDocuments.Contains(active))
                    openDocuments.Add(active);
            }
            catch (Exception exception)
            {
                return BlockedCleanup("OPEN_DOCUMENT_INVENTORY_FAILED",
                    "SolidWorks open-document inventory failed: " + exception.Message,
                    solidWorksPids);
            }
            var closedOwnedDocuments = new JArray();
            int documentsRemainingBeforeExit = 0;
            if (openDocuments.Count != 0)
            {
                bool serviceOwned = String.Equals(ownership,
                    "execution_service_owned_session", StringComparison.Ordinal);
                string[] expected = (expectedOpenDocumentPaths ?? new string[0])
                    .Where(path => !String.IsNullOrWhiteSpace(path))
                    .Select(Path.GetFullPath).Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
                string[] actual = serviceOwned ? new string[0] : openDocuments
                    .Select(document => document.GetPathName() ?? "")
                    .Where(path => !String.IsNullOrWhiteSpace(path))
                    .Select(Path.GetFullPath)
                    .Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
                bool exactExplicitRecovery = !serviceOwned && expected.Length != 0 &&
                    actual.Length == openDocuments.Count &&
                    new HashSet<string>(expected, StringComparer.OrdinalIgnoreCase)
                        .SetEquals(actual) && openDocuments.All(document =>
                            document.IsOpenedReadOnly());
                if (!serviceOwned && !exactExplicitRecovery)
                    return BlockedCleanup("OPEN_DOCUMENTS_PRESENT",
                        "Cleanup refused because open documents are not an exact read-only recovery set.",
                        solidWorksPids, openDocuments.Count);

                try { _solidWorks.UserControl = false; } catch { }
                foreach (IModelDoc2 document in openDocuments)
                {
                    string path = document.GetPathName() ?? "";
                    string title = document.GetTitle();
                    bool readOnly = document.IsOpenedReadOnly();
                    _solidWorks.CloseDoc(title);
                    if (_solidWorks.GetOpenDocumentByName(path) != null)
                    {
                        try { _solidWorks.QuitDoc(title); } catch { }
                    }
                    if (_solidWorks.GetOpenDocumentByName(path) != null)
                    {
                        try { _solidWorks.QuitDoc(path); } catch { }
                    }
                    bool closeConfirmed = _solidWorks.GetOpenDocumentByName(path) == null;
                    closedOwnedDocuments.Add(new JObject
                    {
                        ["path"] = path,
                        ["title"] = title,
                        ["read_only"] = readOnly,
                        ["close_confirmed"] = closeConfirmed
                    });
                }
                Array remainingDocuments = _solidWorks.GetDocuments() as Array;
                documentsRemainingBeforeExit = remainingDocuments == null
                    ? (_solidWorks.IActiveDoc2 == null ? 0 : 1)
                    : remainingDocuments.Length;
            }

            HashSet<int> trackedProcessIds = SnapshotManagedSolidWorksProcessIds();
            ISldWorks application = _solidWorks;
            _solidWorks = null;
            IsConnected = false;
            string gracefulExitError = null;
            try
            {
                // EnsureReady deliberately exposes sessions for interactive CAD work. A session
                // that this lifecycle owns must be returned to automation control before ExitApp;
                // otherwise SolidWorks can remain resident even with no open documents.
                try { application.UserControl = false; } catch { }
                try { application.Visible = false; } catch { }
                application.ExitApp();
            }
            catch (Exception exception)
            {
                gracefulExitError = exception.GetType().Name + ": " + exception.Message;
            }
            finally
            {
                if (application != null && Marshal.IsComObject(application))
                {
                    try { Marshal.FinalReleaseComObject(application); } catch { }
                }
                GC.Collect();
                GC.WaitForPendingFinalizers();
            }

            DateTime deadline = DateTime.UtcNow.AddSeconds(10);
            HashSet<int> remaining;
            do
            {
                remaining = SnapshotManagedSolidWorksProcessIds();
                if (remaining.Count == 0) break;
                Thread.Sleep(250);
            }
            while (DateTime.UtcNow < deadline);

            bool forceTerminationUsed = false;
            JArray forceTerminationErrors = new JArray();
            if (remaining.Count != 0)
            {
                forceTerminationUsed = true;
                forceTerminationErrors = ForceTerminateTrackedProcesses(
                    expectedProcessId, trackedProcessIds);
                deadline = DateTime.UtcNow.AddSeconds(15);
                do
                {
                    remaining = SnapshotManagedSolidWorksProcessIds();
                    if (remaining.Count == 0) break;
                    Thread.Sleep(250);
                }
                while (DateTime.UtcNow < deadline);
            }

            if (remaining.Count != 0)
                return BlockedCleanup("SOLIDWORKS_PROCESSES_REMAIN",
                    "SolidWorks did not fully exit after graceful and bounded forced cleanup.",
                    remaining);

            ClearOwnedSolidWorksSession(expectedProcessId);
            return new JObject
            {
                ["status"] = "pass",
                ["ownership"] = ownership,
                ["expected_process_id"] = expectedProcessId,
                ["exit_app_invoked"] = true,
                ["graceful_exit_error"] = gracefulExitError == null
                    ? JValue.CreateNull()
                    : (JToken)gracefulExitError,
                ["force_termination_used"] = forceTerminationUsed,
                ["force_termination_errors"] = forceTerminationErrors,
                ["closed_owned_documents"] = closedOwnedDocuments,
                ["documents_remaining_before_exit"] = documentsRemainingBeforeExit,
                ["tracked_process_ids"] = new JArray(trackedProcessIds.OrderBy(value => value)),
                ["remaining_process_ids"] = new JArray()
            };
        }

        public ExecutionResponse RunManagedSemanticTask(string toolName,
            Func<ExecutionResponse> operation)
        {
            if (operation == null) throw new ArgumentNullException("operation");
            if (_managedSemanticTask)
                throw new InvalidOperationException("Managed semantic task scopes cannot nest.");
            _managedSemanticTask = true;
            _managedConnectionAttempted = false;
            _managedSessionOwned = false;
            _managedLifecycle = new JObject
            {
                ["tool"] = toolName,
                ["cad_contacted"] = false
            };
            ExecutionResponse response = null;
            try
            {
                response = operation();
                return response;
            }
            finally
            {
                if (_managedConnectionAttempted)
                {
                    JObject cleanup = _managedSessionOwned
                        ? CleanupOwnedSolidWorksSession()
                        : SkippedCleanup("A pre-existing SolidWorks session was preserved.");
                    _managedLifecycle["cleanup"] = cleanup;
                    if (response != null)
                    {
                        JObject resultGeometry = response.ResultGeometry as JObject;
                        if (resultGeometry == null)
                        {
                            resultGeometry = new JObject();
                            response.ResultGeometry = resultGeometry;
                        }
                        resultGeometry["lifecycle"] = _managedLifecycle.DeepClone();
                        if (_managedSessionOwned && !String.Equals(
                            cleanup.Value<string>("status"), "pass", StringComparison.Ordinal))
                        {
                            response.Status = "FAILED";
                            response.Verified = false;
                            response.Error = new ExecutionError
                            {
                                Code = "SOLIDWORKS_SESSION_CLEANUP_BLOCKED",
                                Message = "The semantic operation finished but its Execution Service-owned SolidWorks session did not exit cleanly."
                            };
                        }
                    }
                }
                _managedSemanticTask = false;
                _managedConnectionAttempted = false;
                _managedSessionOwned = false;
            }
        }

        private bool EnsureManagedSemanticConnection()
        {
            if (_managedConnectionAttempted)
                return _solidWorks != null && IsConnected;
            _managedConnectionAttempted = true;
            _managedLifecycle["cad_contacted"] = true;

            int staleOwnedProcessId;
            if (TryGetOwnedSolidWorksProcessId(out staleOwnedProcessId))
            {
                JObject priorCleanup = CleanupOwnedSolidWorksSession();
                _managedLifecycle["prior_owned_session_cleanup"] = priorCleanup;
                if (!String.Equals(priorCleanup.Value<string>("status"), "pass",
                        StringComparison.Ordinal))
                    return false;
            }

            Dictionary<string, object> readiness = EnsureReady();
            _managedLifecycle["ensure_ready"] = JObject.FromObject(readiness);
            int ownedProcessId;
            _managedSessionOwned = TryGetOwnedSolidWorksProcessId(out ownedProcessId);
            _managedLifecycle["session_owned_by_execution_service"] = _managedSessionOwned;
            _managedLifecycle["owned_process_id"] = _managedSessionOwned
                ? (JToken)ownedProcessId : JValue.CreateNull();
            return readiness.ContainsKey("comAttached") &&
                Convert.ToBoolean(readiness["comAttached"]);
        }

        private static JArray ForceTerminateTrackedProcesses(int expectedProcessId,
            HashSet<int> trackedProcessIds)
        {
            var errors = new JArray();
            IEnumerable<int> ordered = trackedProcessIds
                .OrderBy(processId => processId == expectedProcessId ? 0 : 1)
                .ThenBy(processId => processId);
            foreach (int processId in ordered)
            {
                try
                {
                    using (Process process = Process.GetProcessById(processId))
                    {
                        string processName = process.ProcessName;
                        bool exactMain = processId == expectedProcessId &&
                            String.Equals(processName, "SLDWORKS",
                                StringComparison.OrdinalIgnoreCase);
                        bool trackedChild = processId != expectedProcessId &&
                            ManagedSolidWorksProcessNames.Any(name =>
                                String.Equals(name, processName,
                                    StringComparison.OrdinalIgnoreCase));
                        if (!exactMain && !trackedChild)
                        {
                            errors.Add("Refused PID " + processId +
                                " because its process name changed to " + processName + ".");
                            continue;
                        }
                        process.Kill();
                        process.WaitForExit(10000);
                    }
                }
                catch (ArgumentException)
                {
                    // The process exited between the snapshot and the bounded fallback.
                }
                catch (Exception exception)
                {
                    errors.Add("PID " + processId + ": " +
                        exception.GetType().Name + ": " + exception.Message);
                }
            }
            return errors;
        }

        private static void RecordOwnedSolidWorksSession(HashSet<int> processIdsBeforeLaunch)
        {
            HashSet<int> before = processIdsBeforeLaunch ?? new HashSet<int>();
            int[] created = SnapshotSolidWorksProcessIds().Except(before).ToArray();
            int? owned = before.Count == 0 && created.Length == 1
                ? (int?)created[0] : null;
            lock (SessionOwnershipGate)
            {
                _ownedSolidWorksProcessId = owned;
                if (owned.HasValue) WriteOwnershipLease(owned.Value);
                else DeleteOwnershipLease(null);
            }
        }

        private static bool TryGetOwnedSolidWorksProcessId(out int processId)
        {
            lock (SessionOwnershipGate)
            {
                if (_ownedSolidWorksProcessId.HasValue &&
                    ValidateOwnedProcess(_ownedSolidWorksProcessId.Value, null))
                {
                    processId = _ownedSolidWorksProcessId.Value;
                    return true;
                }
                _ownedSolidWorksProcessId = null;
                JObject lease = ReadOwnershipLease();
                int leasedProcessId = lease != null
                    ? lease.Value<int?>("solidworks_process_id") ?? 0 : 0;
                long leasedStartTicks = lease != null
                    ? lease.Value<long?>("process_start_time_utc_ticks") ?? 0L : 0L;
                string leasedExecutor = lease != null
                    ? lease.Value<string>("execution_service_path") : null;
                if (leasedProcessId > 0 && leasedStartTicks > 0 &&
                    String.Equals(leasedExecutor, CurrentExecutionServicePath(),
                        StringComparison.OrdinalIgnoreCase) &&
                    ValidateOwnedProcess(leasedProcessId, leasedStartTicks))
                {
                    _ownedSolidWorksProcessId = leasedProcessId;
                    processId = leasedProcessId;
                    return true;
                }
                DeleteOwnershipLease(null);
                processId = 0;
                return false;
            }
        }

        private static void ClearOwnedSolidWorksSession(int expectedProcessId)
        {
            lock (SessionOwnershipGate)
            {
                if (_ownedSolidWorksProcessId == expectedProcessId)
                    _ownedSolidWorksProcessId = null;
                DeleteOwnershipLease(expectedProcessId);
            }
        }

        private static bool ValidateOwnedProcess(int processId, long? expectedStartTicks)
        {
            try
            {
                using (Process process = Process.GetProcessById(processId))
                {
                    if (!String.Equals(process.ProcessName, "SLDWORKS",
                            StringComparison.OrdinalIgnoreCase))
                        return false;
                    if (expectedStartTicks.HasValue &&
                        process.StartTime.ToUniversalTime().Ticks != expectedStartTicks.Value)
                        return false;
                    return SnapshotSolidWorksProcessIds().Contains(processId);
                }
            }
            catch { return false; }
        }

        private static void WriteOwnershipLease(int processId)
        {
            try
            {
                long startTicks;
                using (Process process = Process.GetProcessById(processId))
                    startTicks = process.StartTime.ToUniversalTime().Ticks;
                var lease = new JObject
                {
                    ["schema_version"] = 1,
                    ["solidworks_process_id"] = processId,
                    ["process_start_time_utc_ticks"] = startTicks,
                    ["execution_service_path"] = CurrentExecutionServicePath(),
                    ["recorded_at_utc"] = DateTime.UtcNow.ToString("o")
                };
                string path = OwnershipLeasePath();
                string temporary = path + ".tmp-" + Guid.NewGuid().ToString("N");
                File.WriteAllText(temporary, lease.ToString(Newtonsoft.Json.Formatting.None),
                    new UTF8Encoding(false));
                if (File.Exists(path))
                    File.Replace(temporary, path, null);
                else
                    File.Move(temporary, path);
            }
            catch { }
        }

        private static JObject ReadOwnershipLease()
        {
            try
            {
                string path = OwnershipLeasePath();
                if (!File.Exists(path)) return null;
                JObject lease = JObject.Parse(File.ReadAllText(path, Encoding.UTF8));
                return lease.Value<int?>("schema_version") == 1 ? lease : null;
            }
            catch { return null; }
        }

        private static void DeleteOwnershipLease(int? expectedProcessId)
        {
            try
            {
                string path = OwnershipLeasePath();
                if (!File.Exists(path)) return;
                if (expectedProcessId.HasValue)
                {
                    JObject lease = ReadOwnershipLease();
                    if (lease != null && lease.Value<int?>("solidworks_process_id") !=
                        expectedProcessId.Value)
                        return;
                }
                File.Delete(path);
            }
            catch { }
        }

        private static string OwnershipLeasePath()
        {
            byte[] bytes = Encoding.UTF8.GetBytes(CurrentExecutionServicePath().ToLowerInvariant());
            string identity;
            using (SHA256 algorithm = SHA256.Create())
                identity = String.Concat(algorithm.ComputeHash(bytes)
                    .Take(8).Select(value => value.ToString("x2")));
            return Path.Combine(Path.GetTempPath(),
                "q3ds-solidworks-session-" + identity + ".json");
        }

        private static string CurrentExecutionServicePath()
        {
            try
            {
                using (Process process = Process.GetCurrentProcess())
                    return Path.GetFullPath(process.MainModule.FileName);
            }
            catch { return Path.GetFullPath(AppDomain.CurrentDomain.BaseDirectory); }
        }

        private static HashSet<int> SnapshotSolidWorksProcessIds()
        {
            return SnapshotProcessIds(new[] { "SLDWORKS" });
        }

        private static HashSet<int> SnapshotManagedSolidWorksProcessIds()
        {
            return SnapshotProcessIds(ManagedSolidWorksProcessNames);
        }

        private static HashSet<int> SnapshotProcessIds(IEnumerable<string> processNames)
        {
            var result = new HashSet<int>();
            foreach (string processName in processNames)
            {
                try
                {
                    foreach (Process process in Process.GetProcessesByName(processName))
                    {
                        try { result.Add(process.Id); }
                        finally { process.Dispose(); }
                    }
                }
                catch { }
            }
            return result;
        }

        private static JObject SkippedCleanup(string reason)
        {
            return new JObject
            {
                ["status"] = "skipped",
                ["reason"] = reason,
                ["remaining_process_ids"] = new JArray()
            };
        }

        private static JObject BlockedCleanup(string code, string message,
            IEnumerable<int> processIds = null, int? openDocumentCount = null)
        {
            var result = new JObject
            {
                ["status"] = "blocked",
                ["error"] = new JObject
                {
                    ["code"] = code,
                    ["message"] = message
                },
                ["remaining_process_ids"] = new JArray(
                    (processIds ?? new int[0]).OrderBy(value => value))
            };
            if (openDocumentCount.HasValue)
                result["open_document_count"] = openDocumentCount.Value;
            return result;
        }
    }
}
