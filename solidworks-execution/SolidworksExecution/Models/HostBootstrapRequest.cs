using Newtonsoft.Json;

namespace SolidworksExecution.Models
{
    public sealed class HostBootstrapRequest
    {
        [JsonProperty("mode")]
        public string Mode { get; set; }
        [JsonProperty("output_directory")]
        public string OutputDirectory { get; set; }
        [JsonProperty("drawing_template_path")]
        public string DrawingTemplatePath { get; set; }
        [JsonProperty("visible")]
        public bool Visible { get; set; }
        [JsonProperty("keep_solidworks_running")]
        public bool KeepSolidworksRunning { get; set; }
        [JsonProperty("com_timeout_seconds")]
        public int ComTimeoutSeconds { get; set; } = 180;
        [JsonProperty("regserver_timeout_seconds")]
        public int RegserverTimeoutSeconds { get; set; } = 120;
    }
}
