using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace SolidworksExecution.Contracts
{
    /// <summary>
    /// Resolves feature-bound center-mark circles from the hash-verified geometry report before
    /// SolidWorks is contacted. Runtime selection still requires a unique visible native edge.
    /// </summary>
    public sealed class ViewPlanCenterGeometryResolver
    {
        public bool TryResolve(ViewPlanBasicExecutionPlan plan,
            out ViewPlanExecutionContractError error)
        {
            error = null;
            if (plan == null || plan.Views == null)
                return Fail("VIEW_PLAN_CENTER_GEOMETRY_INVALID", "",
                    "A compiled ViewPlan is required.", out error);
            var marks = plan.Views.SelectMany(view => view.CenterMarks ??
                new List<ViewPlanCenterMarkSpec>()).ToList();
            if (marks.Count == 0) return true;
            ViewPlanBoundArtifact artifact = plan.InputArtifacts == null ? null :
                plan.InputArtifacts.FirstOrDefault(item => item.Role == "geometry_report");
            if (artifact == null || string.IsNullOrWhiteSpace(artifact.Path))
                return Fail("VIEW_PLAN_CENTER_GEOMETRY_INVALID", "/geometry_report_path",
                    "The frozen geometry-report binding is unavailable.", out error);

            JToken geometry;
            try
            {
                using (var stream = File.OpenText(artifact.Path))
                using (var reader = new JsonTextReader(stream))
                {
                    reader.DateParseHandling = DateParseHandling.None;
                    geometry = JToken.Load(reader, new JsonLoadSettings
                    {
                        DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error,
                        CommentHandling = CommentHandling.Ignore,
                        LineInfoHandling = LineInfoHandling.Ignore
                    });
                    if (reader.Read())
                        throw new InvalidDataException(
                            "The geometry report contains trailing JSON content.");
                }
            }
            catch (Exception ex)
            {
                return Fail("VIEW_PLAN_CENTER_GEOMETRY_INVALID", "/geometry_report_path",
                    "The frozen geometry report is not a unique-key JSON document: " +
                    ex.Message, out error);
            }

            foreach (ViewPlanBasicViewSpec view in plan.Views)
            foreach (ViewPlanCenterMarkSpec mark in view.CenterMarks)
            {
                var resolved = new Dictionary<string, ViewPlanCircularEdgeSpec>(
                    StringComparer.Ordinal);
                for (int featureIndex = 0; featureIndex < mark.FeatureIds.Count; featureIndex++)
                {
                    string featureId = mark.FeatureIds[featureIndex];
                    string pointer = "/views/" + view.OriginalIndex + "/center_marks/" +
                        mark.OriginalIndex + "/feature_ids/" + featureIndex;
                    List<JObject> featureMatches = FindObjects(geometry, featureId).ToList();
                    if (featureMatches.Count != 1)
                        return Fail("VIEW_PLAN_CENTER_FEATURE_AMBIGUOUS", pointer,
                            "Feature '" + featureId + "' resolved " + featureMatches.Count +
                            " times in the frozen geometry report; exactly one is required.",
                            out error);
                    var edgeIds = new HashSet<string>(StringComparer.Ordinal);
                    JObject feature = featureMatches[0];
                    if (string.Equals(feature.Value<string>("curve_type"), "circle",
                        StringComparison.Ordinal)) edgeIds.Add(featureId);
                    CollectEdgeIds(feature, edgeIds);
                    int before = resolved.Count;
                    foreach (string edgeId in edgeIds)
                    {
                        List<JObject> edgeMatches = FindObjects(geometry, edgeId).ToList();
                        if (edgeMatches.Count != 1) continue;
                        double[] center;
                        double[] axis;
                        double radius;
                        if (!TryCircle(edgeMatches[0], out center, out axis, out radius)) continue;
                        resolved[edgeId] = new ViewPlanCircularEdgeSpec
                        {
                            FeatureId = featureId,
                            EdgeId = edgeId,
                            CenterModel = center,
                            AxisModel = axis,
                            RadiusModel = radius
                        };
                    }
                    if (resolved.Count == before)
                        return Fail("VIEW_PLAN_CENTER_FEATURE_CIRCLES_INVALID", pointer,
                            "Feature '" + featureId +
                            "' does not resolve any finite circular B-Rep edge.", out error);
                }
                if (resolved.Count < mark.ExpectedCount)
                    return Fail("VIEW_PLAN_CENTER_EXPECTED_COUNT_INVALID",
                        "/views/" + view.OriginalIndex + "/center_marks/" +
                        mark.OriginalIndex + "/expected_count",
                        "The frozen feature evidence contains fewer circular edges than " +
                        "expected_count.", out error);
                mark.CircularEdges = resolved.Values.OrderBy(item => item.EdgeId,
                    StringComparer.Ordinal).ToList();
            }
            return true;
        }

        private static IEnumerable<JObject> FindObjects(JToken token, string id)
        {
            var obj = token as JObject;
            if (obj != null)
            {
                if (string.Equals(obj.Value<string>("id"), id, StringComparison.Ordinal) ||
                    string.Equals(obj.Value<string>("feature_id"), id,
                        StringComparison.Ordinal)) yield return obj;
                foreach (JProperty property in obj.Properties())
                    foreach (JObject found in FindObjects(property.Value, id)) yield return found;
                yield break;
            }
            var array = token as JArray;
            if (array == null) yield break;
            foreach (JToken child in array)
                foreach (JObject found in FindObjects(child, id)) yield return found;
        }

        private static void CollectEdgeIds(JToken token, ISet<string> result)
        {
            var obj = token as JObject;
            if (obj != null)
            {
                foreach (JProperty property in obj.Properties())
                {
                    if (property.Name == "edge_ids" && property.Value is JArray)
                        foreach (string id in ((JArray)property.Value).Values<string>())
                            if (!string.IsNullOrWhiteSpace(id)) result.Add(id);
                    else CollectEdgeIds(property.Value, result);
                }
                return;
            }
            var array = token as JArray;
            if (array == null) return;
            foreach (JToken child in array) CollectEdgeIds(child, result);
        }

        private static bool TryCircle(JObject edge, out double[] center, out double[] axis,
            out double radius)
        {
            center = null;
            axis = null;
            radius = 0.0;
            if (!string.Equals(edge.Value<string>("curve_type"), "circle",
                StringComparison.Ordinal)) return false;
            var values = edge["curve_parameters"] as JArray;
            if (values == null || values.Count < 7) return false;
            var raw = new double[7];
            for (int index = 0; index < raw.Length; index++)
            {
                JToken token = values[index];
                if ((token.Type != JTokenType.Integer && token.Type != JTokenType.Float) ||
                    double.IsNaN(raw[index] = token.Value<double>()) ||
                    double.IsInfinity(raw[index])) return false;
            }
            double length = Math.Sqrt(raw[3] * raw[3] + raw[4] * raw[4] +
                raw[5] * raw[5]);
            if (length <= 1e-12 || raw[6] <= 0.0) return false;
            center = raw.Take(3).ToArray();
            axis = new[] { raw[3] / length, raw[4] / length, raw[5] / length };
            radius = raw[6];
            return true;
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
}
