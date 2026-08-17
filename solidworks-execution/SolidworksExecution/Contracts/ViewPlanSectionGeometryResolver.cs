using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace SolidworksExecution.Contracts
{
    /// <summary>
    /// Resolves section feature evidence from the already hash-verified frozen geometry report.
    /// This is a COM-free preflight and never infers from live SolidWorks topology.
    /// </summary>
    public sealed class ViewPlanSectionGeometryResolver
    {
        public bool TryResolve(ViewPlanBasicExecutionPlan plan,
            out ViewPlanExecutionContractError error)
        {
            error = null;
            if (plan == null || plan.Views == null)
                return Fail("VIEW_PLAN_SECTION_GEOMETRY_INVALID", "",
                    "A compiled ViewPlan is required.", out error);
            var sections = plan.Views.Where(item => IsSectionType(item.Type)).ToList();
            if (sections.Count == 0) return true;
            ViewPlanBoundArtifact artifact = plan.InputArtifacts == null ? null :
                plan.InputArtifacts.FirstOrDefault(item => item.Role == "geometry_report");
            if (artifact == null || string.IsNullOrWhiteSpace(artifact.Path))
                return Fail("VIEW_PLAN_SECTION_GEOMETRY_INVALID", "/geometry_report_path",
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
                return Fail("VIEW_PLAN_SECTION_GEOMETRY_INVALID", "/geometry_report_path",
                    "The frozen geometry report is not a unique-key JSON document: " + ex.Message,
                    out error);
            }

            foreach (ViewPlanBasicViewSpec section in sections)
            {
                var origins = new List<double[]>();
                var directions = new List<double[]>();
                for (int index = 0; index < section.SectionFeatureIds.Count; index++)
                {
                    string featureId = section.SectionFeatureIds[index];
                    List<JObject> matches = FindFeatures(geometry, featureId).ToList();
                    string pointer = "/views/" + section.OriginalIndex +
                        "/section_definition/feature_ids/" + index;
                    if (matches.Count != 1)
                        return Fail("VIEW_PLAN_SECTION_FEATURE_AMBIGUOUS", pointer,
                            "Feature '" + featureId + "' resolved " + matches.Count +
                            " times in the frozen geometry report; exactly one is required.", out error);
                    if ((section.Type == "full_section" &&
                         section.SectionCuttingLineSource == "derived_feature_axes") ||
                        section.Type == "offset_section")
                    {
                        double[] origin;
                        double[] direction;
                        if (!TryAxis(matches[0], out origin, out direction))
                            return Fail("VIEW_PLAN_SECTION_FEATURE_AXIS_INVALID", pointer,
                                "Feature '" + featureId +
                                "' lacks one finite axis origin and non-zero direction.", out error);
                        if (section.Type == "offset_section" &&
                            !PointOnAnySegment(origin, section.SectionPointsModel))
                            return Fail("VIEW_PLAN_OFFSET_SECTION_AXIS_MISSED", pointer,
                                "The explicit offset cutting path does not intersect feature axis '" +
                                featureId + "'.", out error);
                        origins.Add(origin);
                        directions.Add(direction);
                    }
                }
                section.SectionFeatureAxisOriginsModel = origins;
                section.SectionFeatureAxisDirectionsModel = directions;
            }
            return true;
        }

        private static IEnumerable<JObject> FindFeatures(JToken token, string featureId)
        {
            var obj = token as JObject;
            if (obj != null)
            {
                if (string.Equals(obj.Value<string>("id"), featureId, StringComparison.Ordinal) ||
                    string.Equals(obj.Value<string>("feature_id"), featureId,
                        StringComparison.Ordinal)) yield return obj;
                foreach (JProperty property in obj.Properties())
                    foreach (JObject found in FindFeatures(property.Value, featureId))
                        yield return found;
                yield break;
            }
            var array = token as JArray;
            if (array == null) yield break;
            foreach (JToken child in array)
                foreach (JObject found in FindFeatures(child, featureId)) yield return found;
        }

        private static bool TryAxis(JObject feature, out double[] origin, out double[] direction)
        {
            origin = null;
            direction = null;
            var candidates = new List<JObject> { feature };
            foreach (string key in new[] { "surface_parameters", "axis", "geometry" })
            {
                var child = feature[key] as JObject;
                if (child != null) candidates.Add(child);
            }
            foreach (JObject candidate in candidates)
            {
                if (origin == null)
                    origin = FirstVector(candidate, new[] { "origin", "center", "point_on_axis",
                        "axis_origin", "origin_model_m", "center_model_m" }, false);
                if (direction == null)
                    direction = FirstVector(candidate, new[] { "axis", "direction",
                        "axis_direction", "direction_model" }, true);
                if (origin != null && direction != null) return true;
            }
            return false;
        }

        private static bool PointOnAnySegment(double[] point, IList<double[]> points)
        {
            if (point == null || points == null) return false;
            for (int index = 0; index + 1 < points.Count; index++)
                if (PointOnSegment(point, points[index], points[index + 1])) return true;
            return false;
        }

        private static bool PointOnSegment(double[] point, double[] first, double[] second)
        {
            double[] segment = { second[0] - first[0], second[1] - first[1],
                second[2] - first[2] };
            double lengthSquared = segment[0] * segment[0] + segment[1] * segment[1] +
                segment[2] * segment[2];
            if (lengthSquared <= 1e-24) return false;
            double[] delta = { point[0] - first[0], point[1] - first[1],
                point[2] - first[2] };
            double parameter = (delta[0] * segment[0] + delta[1] * segment[1] +
                delta[2] * segment[2]) / lengthSquared;
            if (parameter < -1e-8 || parameter > 1.0 + 1e-8) return false;
            double distanceSquared = 0.0;
            for (int axis = 0; axis < 3; axis++)
            {
                double residual = point[axis] - (first[axis] + parameter * segment[axis]);
                distanceSquared += residual * residual;
            }
            double tolerance = Math.Max(Math.Sqrt(lengthSquared) * 1e-6, 1e-9);
            return distanceSquared <= tolerance * tolerance;
        }

        private static bool IsSectionType(string type)
        {
            return type == "full_section" || type == "half_section" ||
                type == "offset_section" || type == "aligned_section" ||
                type == "removed_section";
        }

        private static double[] FirstVector(JObject source, IEnumerable<string> names,
            bool requireNonZero)
        {
            foreach (string name in names)
            {
                var array = source[name] as JArray;
                if (array == null || array.Count != 3) continue;
                var value = new double[3];
                bool valid = true;
                for (int index = 0; index < 3; index++)
                {
                    JToken token = array[index];
                    if (token.Type != JTokenType.Integer && token.Type != JTokenType.Float)
                    {
                        valid = false;
                        break;
                    }
                    value[index] = token.Value<double>();
                    if (double.IsNaN(value[index]) || double.IsInfinity(value[index]))
                    {
                        valid = false;
                        break;
                    }
                }
                if (!valid) continue;
                double length = Math.Sqrt(value[0] * value[0] + value[1] * value[1] +
                    value[2] * value[2]);
                if (requireNonZero && length <= 1e-12) continue;
                if (requireNonZero)
                    for (int index = 0; index < 3; index++) value[index] /= length;
                return value;
            }
            return null;
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
