using Microsoft.Win32;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

namespace SolidWorksHostBootstrap
{
    internal sealed class Options
    {
        public bool Execute, Visible, KeepRunning, SkipLaunch, NoRegserver, ProbeChild, Help;
        public string OutputDir = ".", Template, Log, LockFile, ProbeResult;
        public double LockTimeout, StaleLock = 7200, RegserverTimeout = 120, ComTimeout = 180;
        public readonly List<string> Deprecated = new List<string>();

        public static Options Parse(string[] args)
        {
            Options value = new Options();
            for (int i = 0; i < args.Length; i++)
            {
                string arg = args[i];
                switch (arg)
                {
                    case "--execute": value.Execute = true; break;
                    case "--visible": value.Visible = true; break;
                    case "--keep-solidworks-running": value.KeepRunning = true; break;
                    case "--quit-solidworks": value.KeepRunning = false; break;
                    case "--skip-solidworks-launch": value.SkipLaunch = true; break;
                    case "--no-regserver": value.NoRegserver = true; break;
                    case "--shared-solidworks": case "--isolated-solidworks": break;
                    case "--output-dir": value.OutputDir = Next(args, ref i, arg); break;
                    case "--template": value.Template = Next(args, ref i, arg); break;
                    case "--log": value.Log = Next(args, ref i, arg); break;
                    case "--lock-file": value.LockFile = Next(args, ref i, arg); break;
                    case "--lock-timeout-seconds": value.LockTimeout = Number(Next(args, ref i, arg), arg); break;
                    case "--stale-lock-seconds": value.StaleLock = Number(Next(args, ref i, arg), arg); break;
                    case "--regserver-timeout-seconds": value.RegserverTimeout = Number(Next(args, ref i, arg), arg); break;
                    case "--com-timeout-seconds": value.ComTimeout = Number(Next(args, ref i, arg), arg); break;
                    case "--probe-child": value.ProbeChild = true; break;
                    case "--probe-result": value.ProbeResult = Next(args, ref i, arg); break;
                    case "--help": case "-h": value.Help = true; break;
                    case "--install-pywin32": case "--no-install-pywin32": case "--makepy": case "--no-makepy": value.Deprecated.Add(arg); break;
                    case "--max-transient-retries": case "--retry-delay-seconds": value.Deprecated.Add(arg); Next(args, ref i, arg); break;
                    default: throw new ArgumentException("Unknown argument: " + arg);
                }
            }
            return value;
        }
        private static string Next(string[] args, ref int i, string arg) { if (++i >= args.Length) throw new ArgumentException("Missing value for " + arg); return args[i]; }
        private static double Number(string text, string arg) { double n; if (!double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out n) || n < 0) throw new ArgumentException("Invalid value for " + arg); return n; }
    }

    internal sealed class Report
    {
        public readonly Dictionary<string, object> Root = new Dictionary<string, object>();
        private readonly List<object> warnings = new List<object>(), blockers = new List<object>(), actions = new List<object>();
        public Report(Options o)
        {
            Root["schema_version"] = 2; Root["status"] = "blocked";
            foreach (string name in new[] { "host", "runtime", "paths", "dependencies", "solidworks", "solidworks_installation", "template", "output_dir_check", "elevation" }) Root[name] = new Dictionary<string, object>();
            Root["python"] = D("required", false, "status", "not_required_by_native_bootstrap", "note", "Native bootstrap does not require Python or pywin32.");
            Root["bootstrap_policy"] = D("native_windows_com", true, "requires_python", false, "requires_makepy", false, "auto_regserver_repair", o.Execute && !o.NoRegserver, "com_probe_isolated_process", true, "abort_on_objective_blocker", true, "solidworks_product_installation", "detect_only");
            Root["actions"] = actions; Root["temporary_files"] = new List<object>(); Root["blocking_issues"] = blockers; Root["warnings"] = warnings; Root["generated_at"] = DateTime.UtcNow.ToString("o");
        }
        public Dictionary<string, object> S(string name) { return (Dictionary<string, object>)Root[name]; }
        public void Warn(string text) { if (!warnings.Contains(text)) warnings.Add(text); }
        public void Block(string text) { if (!blockers.Contains(text)) blockers.Add(text); }
        public void Action(Dictionary<string, object> value) { actions.Add(value); }
        public bool Blocked { get { return blockers.Count > 0; } }
        public void Finish() { Root["status"] = blockers.Count > 0 ? "blocked" : warnings.Count > 0 ? "warning" : "pass"; }
        public static Dictionary<string, object> D(params object[] values) { Dictionary<string, object> d = new Dictionary<string, object>(); for (int i = 0; i < values.Length; i += 2) d[(string)values[i]] = values[i + 1]; return d; }
    }

    internal static class Json
    {
        public static void Write(string file, object value) { string full = Path.GetFullPath(file); Directory.CreateDirectory(Path.GetDirectoryName(full)); JavaScriptSerializer s = new JavaScriptSerializer { MaxJsonLength = Int32.MaxValue, RecursionLimit = 100 }; File.WriteAllText(full, s.Serialize(value), new UTF8Encoding(false)); }
        public static Dictionary<string, object> Read(string file) { return new JavaScriptSerializer { MaxJsonLength = Int32.MaxValue, RecursionLimit = 100 }.Deserialize<Dictionary<string, object>>(File.ReadAllText(file, Encoding.UTF8)); }
    }

    internal sealed class AutomationLock : IDisposable
    {
        private readonly string file; private FileStream stream;
        public AutomationLock(string path, double wait, double stale)
        {
            file = Path.GetFullPath(path); DateTime deadline = DateTime.UtcNow.AddSeconds(wait);
            while (true)
            {
                try { if (stale > 0 && File.Exists(file) && (DateTime.UtcNow - File.GetLastWriteTimeUtc(file)).TotalSeconds > stale) File.Delete(file); } catch { }
                try { Directory.CreateDirectory(Path.GetDirectoryName(file)); stream = new FileStream(file, FileMode.CreateNew, FileAccess.Write, FileShare.None); byte[] data = Encoding.UTF8.GetBytes("pid=" + Process.GetCurrentProcess().Id); stream.Write(data, 0, data.Length); stream.Flush(); return; }
                catch (IOException) { if (DateTime.UtcNow >= deadline) throw new InvalidOperationException("SolidWorks automation lock is held: " + file); Thread.Sleep(500); }
            }
        }
        public void Dispose() { if (stream != null) stream.Dispose(); try { File.Delete(file); } catch { } }
    }

    internal sealed class Installation
    {
        public string View, ProgId, Clsid, LocalServer, Exe, Version; public bool Exists, TypeLib; public readonly List<string> Interop = new List<string>();
        public Dictionary<string, object> Data() { return Report.D("registry_view", View, "progid", ProgId, "clsid", Clsid, "local_server32", LocalServer, "executable", Exe, "executable_exists", Exists, "file_version", Version, "type_library_exists", TypeLib, "interop_assemblies", Interop.ToArray()); }
    }

    internal static class Checks
    {
        public static void Host(Report r)
        {
            Dictionary<string, object> h = r.S("host"); h["machine_name"] = Environment.MachineName; h["user_name"] = Environment.UserDomainName + "\\" + Environment.UserName; h["os_version"] = Environment.OSVersion.VersionString; h["is_64_bit_os"] = Environment.Is64BitOperatingSystem; h["is_64_bit_process"] = Environment.Is64BitProcess; h["user_interactive"] = Environment.UserInteractive; h["session_id"] = Process.GetCurrentProcess().SessionId; h["current_directory"] = Environment.CurrentDirectory;
            bool elevated = false; try { elevated = new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator); } catch { }
            r.S("elevation")["current_process_elevated"] = elevated; r.S("elevation")["required_for_com"] = false;
            r.S("runtime")["framework"] = ".NET Framework " + Environment.Version; r.S("runtime")["executable"] = Assembly.GetExecutingAssembly().Location; r.S("runtime")["process_architecture"] = Environment.Is64BitProcess ? "x64" : "x86";
            if (!Environment.Is64BitOperatingSystem) r.Block("Modern SolidWorks automation requires 64-bit Windows."); if (!Environment.Is64BitProcess) r.Block("Bootstrap must run as x64."); if (!Environment.UserInteractive || Process.GetCurrentProcess().SessionId == 0) r.Block("SolidWorks COM must run in an interactive desktop session, not Session 0 or a service.");
        }
        public static void Output(Options o, Report r)
        {
            string dir = Path.GetFullPath(o.OutputDir), probe = Path.Combine(dir, ".solidworks-host-bootstrap-write-test.tmp"); Dictionary<string, object> d = r.S("output_dir_check"); d["path"] = dir; d["exists"] = Directory.Exists(dir); d["writable"] = false; d["probe"] = probe;
            try { Directory.CreateDirectory(dir); File.WriteAllText(probe, "ok"); d["writable"] = File.ReadAllText(probe) == "ok"; } catch (Exception e) { d["error"] = e.Message; r.Block("Output directory is not writable: " + dir); } finally { try { File.Delete(probe); } catch { } }
        }
        public static void Template(Options o, Report r)
        {
            Dictionary<string, object> d = r.S("template"); d["provided"] = !String.IsNullOrWhiteSpace(o.Template); if (String.IsNullOrWhiteSpace(o.Template)) return; string file = Path.GetFullPath(o.Template); bool exists = File.Exists(file), suffix = String.Equals(Path.GetExtension(file), ".drwdot", StringComparison.OrdinalIgnoreCase); d["path"] = file; d["exists"] = exists; d["suffix_ok"] = suffix; bool readable = false; try { using (FileStream s = new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.ReadWrite)) readable = true; } catch { } d["readable"] = readable; if (!exists) r.Block("Template does not exist: " + file); else if (!suffix) r.Block("Template must use .DRWDOT: " + file); else if (!readable) r.Block("Template is not readable: " + file); if (file.StartsWith("\\\\")) r.Warn("UNC templates are less reliable than local templates.");
        }
        public static List<Installation> Installations(Report r)
        {
            List<Installation> list = new List<Installation>(); HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase); string env = Environment.GetEnvironmentVariable("SOLIDWORKS_EXE"); if (!String.IsNullOrWhiteSpace(env)) Add(list, seen, Make("environment", "SOLIDWORKS_EXE", null, null, env)); Scan(RegistryView.Registry64, list, seen); Scan(RegistryView.Registry32, list, seen);
            List<object> data = new List<object>(); foreach (Installation x in list) data.Add(x.Data());
            HashSet<string> usable = new HashSet<string>(StringComparer.OrdinalIgnoreCase); foreach (Installation x in list) if (x.Exists && !String.IsNullOrWhiteSpace(x.Exe)) usable.Add(x.Exe);
            Dictionary<string, object> d = r.S("solidworks_installation"); d["installations"] = data; d["count"] = list.Count; d["usable_count"] = usable.Count;
            Installation selected = Select(list); if (selected == null) { d["executable_exists"] = false; r.Block("SolidWorks was not discovered from SOLIDWORKS_EXE or COM registration."); } else { d["selected"] = selected.Data(); d["progid"] = selected.ProgId; d["clsid"] = selected.Clsid; d["local_server32"] = selected.LocalServer; d["executable"] = selected.Exe; d["executable_exists"] = selected.Exists; }
            if (usable.Count > 1) r.Warn("Multiple usable SolidWorks installations were discovered; active non-versioned registration is preferred."); return list;
        }
        public static Installation Select(List<Installation> list) { foreach (Installation x in list) if (x.Exists && x.ProgId == "SldWorks.Application") return x; foreach (Installation x in list) if (x.Exists) return x; return null; }
        private static void Scan(RegistryView view, List<Installation> list, HashSet<string> seen) { try { using (RegistryKey root = RegistryKey.OpenBaseKey(RegistryHive.ClassesRoot, view)) { AddProg(root, view.ToString(), "SldWorks.Application", list, seen); foreach (string key in root.GetSubKeyNames()) if (key.StartsWith("SldWorks.Application.", StringComparison.OrdinalIgnoreCase)) AddProg(root, view.ToString(), key, list, seen); } } catch { } }
        private static void AddProg(RegistryKey root, string view, string prog, List<Installation> list, HashSet<string> seen) { string clsid = Def(root, prog + "\\CLSID"); if (String.IsNullOrWhiteSpace(clsid)) return; string server = Def(root, "CLSID\\" + clsid + "\\LocalServer32"); Add(list, seen, Make(view, prog, clsid, server, ParseExe(server))); }
        private static string Def(RegistryKey root, string path) { using (RegistryKey key = root.OpenSubKey(path)) return key == null ? null : Convert.ToString(key.GetValue(null)); }
        private static Installation Make(string view, string prog, string clsid, string server, string exe) { Installation x = new Installation { View = view, ProgId = prog, Clsid = clsid, LocalServer = server }; if (!String.IsNullOrWhiteSpace(exe)) { try { exe = Path.GetFullPath(Environment.ExpandEnvironmentVariables(exe)); } catch { } x.Exe = exe; x.Exists = File.Exists(exe); if (x.Exists) { try { x.Version = FileVersionInfo.GetVersionInfo(exe).FileVersion; } catch { } string dir = Path.GetDirectoryName(exe); x.TypeLib = File.Exists(Path.Combine(dir, "sldworks.tlb")); foreach (string n in new[] { "SolidWorks.Interop.sldworks.dll", "SolidWorks.Interop.swconst.dll", "SolidWorks.Interop.swpublished.dll" }) { string p = Path.Combine(dir, n); if (File.Exists(p)) x.Interop.Add(p); } } } return x; }
        private static void Add(List<Installation> list, HashSet<string> seen, Installation x) { string key = String.IsNullOrWhiteSpace(x.Exe) ? x.ProgId + "|" + x.Clsid : x.Exe; if (seen.Add(key)) list.Add(x); }
        private static string ParseExe(string text) { if (String.IsNullOrWhiteSpace(text)) return null; text = text.Trim(); if (text.StartsWith("\"")) { int end = text.IndexOf('"', 1); if (end > 1) return text.Substring(1, end - 1); } int i = text.IndexOf(".exe", StringComparison.OrdinalIgnoreCase); return i >= 0 ? text.Substring(0, i + 4) : text; }
    }

    internal static class Probe
    {
        public static bool Run(Options o, Report r, Installation selected)
        {
            Dictionary<string, object> sw = r.S("solidworks"); sw["attempted"] = false; sw["dispatch"] = "skipped"; sw["version"] = null; sw["revision"] = null; sw["visible"] = o.Visible; sw["quit_attempted"] = false; sw["errors"] = new List<object>();
            if (o.SkipLaunch) { r.Warn("SolidWorks launch skipped by --skip-solidworks-launch."); return true; } if (!o.Execute) { r.Warn("Run with --execute to complete the isolated COM probe."); return true; } if (selected == null) return false; sw["attempted"] = true;
            Dictionary<string, object> result = Child(o, selected, r); Merge(sw, result); if (Pass(result)) return true;
            bool elevated = Convert.ToBoolean(r.S("elevation")["current_process_elevated"]); if (!elevated) { r.S("elevation")["required_for_com"] = null; r.S("elevation")["unrestricted_process_recommended"] = true; r.Block("SolidWorks COM activation failed in the current process context."); r.Block("Retry from an unrestricted interactive desktop process. Use elevation only if that standard process also fails and COM registration repair is required."); Crashes(sw); return false; }
            if (!o.NoRegserver) { Regserver(o, selected, r); Dictionary<string, object> retry = Child(o, selected, r); sw["retry_after_regserver"] = retry; Merge(sw, retry); if (Pass(retry)) return true; }
            r.Block("SolidWorks COM server failed after available automated repairs."); Crashes(sw); return false;
        }
        public static int ChildMode(Options o)
        {
            Dictionary<string, object> result = new Dictionary<string, object>(); object app = null; HashSet<int> before = Pids();
            try { Type type = Type.GetTypeFromProgID("SldWorks.Application", true); app = Activator.CreateInstance(type); Set(app, "Visible", o.Visible); object revision = Invoke(app, "RevisionNumber"); HashSet<int> after = Pids(); List<int> created = Diff(after, before); bool owned = created.Count > 0 || before.Count == 0; result["status"] = "pass"; result["dispatch"] = "CoCreateInstance"; result["version"] = revision == null ? null : Convert.ToString(revision); result["revision"] = result["version"]; result["created_process_ids"] = created.ToArray(); result["session_ownership"] = owned ? "created_or_owned" : "shared_or_unknown"; result["quit_attempted"] = false; if (!o.KeepRunning && owned) { result["quit_attempted"] = true; Invoke(app, "ExitApp"); } else if (!owned) result["quit_skipped_reason"] = "Existing process detected; possibly user-owned session was preserved."; Json.Write(o.ProbeResult, result); return 0; }
            catch (Exception e) { result["status"] = "blocked"; result["error"] = e.ToString(); try { Json.Write(o.ProbeResult, result); } catch { } return 2; }
            finally { if (app != null && Marshal.IsComObject(app)) try { Marshal.FinalReleaseComObject(app); } catch { } GC.Collect(); GC.WaitForPendingFinalizers(); }
        }
        private static Dictionary<string, object> Child(Options o, Installation selected, Report r)
        {
            string resultFile = Path.Combine(Path.GetFullPath(o.OutputDir), ".solidworks-com-probe-" + Guid.NewGuid().ToString("N") + ".json"); HashSet<int> before = Pids(); Dictionary<string, object> action = Report.D("name", "solidworks_com_probe", "status", "not_run", "timeout_seconds", o.ComTimeout, "executable", selected.Exe); r.Action(action);
            try { ProcessStartInfo si = new ProcessStartInfo(Assembly.GetExecutingAssembly().Location, "--probe-child --probe-result \"" + resultFile + "\"" + (o.Visible ? " --visible" : "") + (o.KeepRunning ? " --keep-solidworks-running" : "")) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true, WorkingDirectory = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location) }; using (Process p = Process.Start(si)) { if (!p.WaitForExit((int)Math.Min(Int32.MaxValue, o.ComTimeout * 1000))) { action["status"] = "blocked"; action["timed_out"] = true; try { p.Kill(); } catch { } KillNew(before); return Report.D("status", "blocked", "error", "SolidWorks COM activation timed out.", "timed_out", true); } string stdout = p.StandardOutput.ReadToEnd(), stderr = p.StandardError.ReadToEnd(); action["returncode"] = p.ExitCode; action["stdout_tail"] = Tail(stdout, 2000); action["stderr_tail"] = Tail(stderr, 2000); if (File.Exists(resultFile)) { Dictionary<string, object> value = Json.Read(resultFile); action["status"] = Pass(value) ? "pass" : "blocked"; return value; } action["status"] = "blocked"; return Report.D("status", "blocked", "error", "COM probe child produced no result."); } }
            catch (Exception e) { action["status"] = "blocked"; action["error"] = e.Message; return Report.D("status", "blocked", "error", e.ToString()); }
            finally { try { File.Delete(resultFile); } catch { } }
        }
        private static void Regserver(Options o, Installation selected, Report r) { Dictionary<string, object> a = Report.D("name", "solidworks_regserver", "status", "not_run", "command", new[] { selected.Exe, "/regserver" }); r.Action(a); try { using (Process p = Process.Start(new ProcessStartInfo(selected.Exe, "/regserver") { UseShellExecute = false, CreateNoWindow = true })) { if (!p.WaitForExit((int)Math.Min(Int32.MaxValue, o.RegserverTimeout * 1000))) { a["status"] = "warning"; a["timed_out_seconds"] = o.RegserverTimeout; try { p.Kill(); } catch { } r.Warn("SolidWorks /regserver timed out."); return; } a["returncode"] = p.ExitCode; a["status"] = p.ExitCode == 0 ? "pass" : "warning"; if (p.ExitCode != 0) r.Warn("SolidWorks /regserver returned " + p.ExitCode); } } catch (Exception e) { a["status"] = "warning"; a["error"] = e.Message; r.Warn("SolidWorks /regserver failed: " + e.Message); } }
        private static object Invoke(object x, string name) { return x.GetType().InvokeMember(name, BindingFlags.InvokeMethod | BindingFlags.GetProperty, null, x, null, CultureInfo.InvariantCulture); }
        private static void Set(object x, string name, object value) { x.GetType().InvokeMember(name, BindingFlags.SetProperty, null, x, new[] { value }, CultureInfo.InvariantCulture); }
        private static bool Pass(Dictionary<string, object> x) { object status; return x.TryGetValue("status", out status) && Convert.ToString(status) == "pass"; }
        private static void Merge(Dictionary<string, object> a, Dictionary<string, object> b) { foreach (KeyValuePair<string, object> p in b) a[p.Key] = p.Value; }
        private static HashSet<int> Pids() { HashSet<int> x = new HashSet<int>(); try { foreach (Process p in Process.GetProcessesByName("SLDWORKS")) { x.Add(p.Id); p.Dispose(); } } catch { } return x; }
        private static List<int> Diff(HashSet<int> after, HashSet<int> before) { List<int> x = new List<int>(); foreach (int id in after) if (!before.Contains(id)) x.Add(id); return x; }
        private static void KillNew(HashSet<int> before) { foreach (int id in Diff(Pids(), before)) try { using (Process p = Process.GetProcessById(id)) p.Kill(); } catch { } }
        private static void Crashes(Dictionary<string, object> sw) { List<object> values = new List<object>(); try { using (EventLog log = new EventLog("Application")) { int min = Math.Max(0, log.Entries.Count - 400); for (int i = log.Entries.Count - 1; i >= min && values.Count < 8; i--) { EventLogEntry e = log.Entries[i]; if (e.TimeGenerated < DateTime.Now.AddHours(-2)) break; string m = null; try { m = e.Message; } catch { } if (!String.IsNullOrEmpty(m) && m.IndexOf("SLDWORKS.exe", StringComparison.OrdinalIgnoreCase) >= 0) values.Add(Report.D("time_created", e.TimeGenerated.ToUniversalTime().ToString("o"), "source", e.Source, "event_id", e.InstanceId, "entry_type", e.EntryType.ToString(), "message", Tail(m, 4000))); } } } catch (Exception e) { sw["crash_diagnostics_error"] = e.Message; } sw["recent_crashes"] = values; }
        private static string Tail(string x, int n) { return String.IsNullOrEmpty(x) || x.Length <= n ? x : x.Substring(x.Length - n); }
    }

    internal static class Program
    {
        [STAThread] private static int Main(string[] args)
        {
            try { Options o = Options.Parse(args); if (o.Help) { Help(); return 0; } if (o.ProbeChild) { if (String.IsNullOrWhiteSpace(o.ProbeResult)) throw new ArgumentException("--probe-result is required"); return Probe.ChildMode(o); } return Run(o); }
            catch (Exception e) { Console.Error.WriteLine("ERROR: " + e); return 1; }
        }
        private static int Run(Options o)
        {
            o.OutputDir = Path.GetFullPath(o.OutputDir); Directory.CreateDirectory(o.OutputDir); Report r = new Report(o); foreach (string x in o.Deprecated) r.Warn(x + " is accepted for compatibility but is unnecessary for the native bootstrap."); Checks.Host(r); Checks.Output(o, r); Checks.Template(o, r); List<Installation> list = Checks.Installations(r); Installation selected = Checks.Select(list); r.S("dependencies")["solidworks_interop_required_by_bootstrap"] = false; r.S("dependencies")["python_required_by_bootstrap"] = false; r.S("paths")["output_dir"] = o.OutputDir; r.S("paths")["template"] = o.Template == null ? null : Path.GetFullPath(o.Template);
            string lockFile = String.IsNullOrWhiteSpace(o.LockFile) ? Path.Combine(o.OutputDir, ".solidworks-cli.lock") : Path.GetFullPath(o.LockFile); if (o.Execute && !o.SkipLaunch && !r.Blocked) { try { using (new AutomationLock(lockFile, o.LockTimeout, o.StaleLock)) Probe.Run(o, r, selected); } catch (Exception e) { r.Block("SolidWorks lock/preflight failed: " + e.Message); } } else if (!o.Execute && !o.SkipLaunch) Probe.Run(o, r, selected);
            r.Finish(); string reportFile = Path.Combine(o.OutputDir, "host-preflight-report.json"); Json.Write(reportFile, r.Root); if (!String.IsNullOrWhiteSpace(o.Log)) Json.Write(o.Log, r.Root); Console.WriteLine(reportFile); return r.Blocked ? 2 : 0;
        }
        private static void Help() { Console.WriteLine("SolidWorksHostBootstrap.exe --execute --output-dir <dir> [--template file.DRWDOT] [--visible] [--keep-solidworks-running] [--skip-solidworks-launch] [--com-timeout-seconds 180] [--no-regserver]"); }
    }
}
