using Newtonsoft.Json.Linq;

namespace SolidworksExecution.Models
{
    public sealed class HostBootstrapResponse
    {
        public bool Ok { get; set; }
        public string Status { get; set; }
        public string Mode { get; set; }
        public string ReportPath { get; set; }
        public string ReportSha256 { get; set; }
        public string HelperSha256 { get; set; }
        public int ExitCode { get; set; }
        public JObject Report { get; set; }
        public string StdoutTail { get; set; }
        public string StderrTail { get; set; }
    }
}
