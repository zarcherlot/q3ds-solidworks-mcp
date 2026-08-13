using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading;
using Newtonsoft.Json.Linq;
using SolidWorks.Interop.sldworks;
using SolidworksExecution.Contracts;

namespace SolidworksExecution.Services
{
    public partial class SolidWorksService
    {
        private static readonly object SessionOwnershipGate = new object();
        private static int? _ownedSolidWorksProcessId;
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
            bool allowUnownedIdleSession)
        {
            if (!allowUnownedIdleSession)
                return BlockedCleanup("EXPLICIT_CLEANUP_NOT_AUTHORIZED",
                    "allow_unowned_idle_session=true is required.");
            if (expectedProcessId <= 0)
                return BlockedCleanup("EXPECTED_PROCESS_ID_REQUIRED",
                    "A positive expected_process_id is required.");
            return CleanupSolidWorksSession(expectedProcessId, true,
                "explicit_idle_session_cleanup");
        }

        private JObject CleanupOwnedSolidWorksSession()
        {
            int ownedProcessId;
            if (!TryGetOwnedSolidWorksProcessId(out ownedProcessId))
                return SkippedCleanup("No live Execution Service-owned session was recorded.");
            return CleanupSolidWorksSession(ownedProcessId, true,
                "execution_service_owned_session");
        }

        private JObject CleanupSolidWorksSession(int expectedProcessId, bool authorized,
            string ownership)
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

            if (!EnsureConnected())
                return BlockedCleanup("COM_ATTACH_FAILED",
                    "The exact SolidWorks process exists but COM attachment failed.",
                    solidWorksPids);

            int openDocumentCount;
            try
            {
                object[] documents = _solidWorks.GetDocuments() as object[];
                openDocumentCount = documents == null ? 0 : documents.Length;
                if (openDocumentCount == 0 && _solidWorks.IActiveDoc2 != null)
                    openDocumentCount = 1;
            }
            catch (Exception exception)
            {
                return BlockedCleanup("OPEN_DOCUMENT_INVENTORY_FAILED",
                    "SolidWorks open-document inventory failed: " + exception.Message,
                    solidWorksPids);
            }
            if (openDocumentCount != 0)
                return BlockedCleanup("OPEN_DOCUMENTS_PRESENT",
                    "Cleanup refused because SolidWorks still has open documents.",
                    solidWorksPids, openDocumentCount);

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
                ["tracked_process_ids"] = new JArray(trackedProcessIds.OrderBy(value => value)),
                ["remaining_process_ids"] = new JArray()
            };
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
            lock (SessionOwnershipGate)
            {
                _ownedSolidWorksProcessId = before.Count == 0 && created.Length == 1
                    ? (int?)created[0]
                    : null;
            }
        }

        private static bool TryGetOwnedSolidWorksProcessId(out int processId)
        {
            lock (SessionOwnershipGate)
            {
                if (_ownedSolidWorksProcessId.HasValue &&
                    SnapshotSolidWorksProcessIds().Contains(_ownedSolidWorksProcessId.Value))
                {
                    processId = _ownedSolidWorksProcessId.Value;
                    return true;
                }
                _ownedSolidWorksProcessId = null;
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
            }
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
