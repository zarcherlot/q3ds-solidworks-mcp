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
                ViewPlanBasicViewSpec parent = section.Type == "half_section"
                    ? plan.Views.FirstOrDefault(item => item.Id == section.ParentId) : null;
                ProjectionBasis projection = null;
                IList<double[]> projectedPoints = null;
                if (section.Type == "half_section" &&
                    !TryValidateHalfSection(section, parent, geometry, out projection,
                        out projectedPoints, out error)) return false;
                if (section.Type == "half_section" && section.SectionDepthAutomatic)
                {
                    double[] depthMinimum;
                    double[] depthMaximum;
                    if (!TryPartBox(geometry, out depthMinimum, out depthMaximum))
                        return Fail("VIEW_PLAN_HALF_SECTION_PART_BOX_INVALID",
                            "/geometry_report_path",
                            "Automatic half-section depth requires a finite positive-volume " +
                            "part_box_m.", out error);
                    double minimumProjection = double.PositiveInfinity;
                    double maximumProjection = double.NegativeInfinity;
                    foreach (double[] corner in BoxCorners(depthMinimum, depthMaximum))
                    {
                        double value = Dot(corner, projection.ViewDirection);
                        minimumProjection = Math.Min(minimumProjection, value);
                        maximumProjection = Math.Max(maximumProjection, value);
                    }
                    double frozenDepthSpan = maximumProjection - minimumProjection;
                    if (!Finite(frozenDepthSpan) || frozenDepthSpan <= 1e-12)
                        return Fail("VIEW_PLAN_HALF_SECTION_DEPTH_INVALID",
                            "/views/" + section.OriginalIndex +
                            "/section_definition/section_depth_m",
                            "The frozen part box has no positive depth in the parent view.",
                            out error);
                    section.SectionDepth = frozenDepthSpan * 1.1;
                }
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
                    if (section.Type == "full_section" || section.Type == "half_section" ||
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
                        if (section.Type == "half_section" &&
                            !ProjectedAxisIntersectsSegments(origin, direction, projection,
                                projectedPoints))
                            return Fail("VIEW_PLAN_HALF_SECTION_AXIS_MISSED", pointer,
                                "The explicit half-section path does not intersect feature axis '" +
                                featureId + "' in the parent view.", out error);
                        origins.Add(origin);
                        directions.Add(direction);
                    }
                }
                section.SectionFeatureAxisOriginsModel = origins;
                section.SectionFeatureAxisDirectionsModel = directions;
            }
            return true;
        }

        private static bool TryValidateHalfSection(ViewPlanBasicViewSpec section,
            ViewPlanBasicViewSpec parent, JToken geometry, out ProjectionBasis projection,
            out IList<double[]> projectedPoints, out ViewPlanExecutionContractError error)
        {
            projection = null;
            projectedPoints = null;
            error = null;
            string pointer = "/views/" + section.OriginalIndex +
                "/section_definition/cutting_line_points_model_m";
            if (!TryProjectionBasis(parent, out projection))
                return Fail("VIEW_PLAN_HALF_SECTION_PARENT_PROJECTION_INVALID",
                    "/views/" + section.OriginalIndex + "/parent_view_id",
                    "The half-section parent orientation does not provide a deterministic " +
                    "model-to-view projection.", out error);
            double[] minimum;
            double[] maximum;
            if (!TryPartBox(geometry, out minimum, out maximum))
                return Fail("VIEW_PLAN_HALF_SECTION_PART_BOX_INVALID", "/geometry_report_path",
                    "Half-section validation requires a finite positive-volume part_box_m.",
                    out error);
            ProjectionBasis basis = projection;
            double modelSpan = Math.Max(maximum[0] - minimum[0],
                Math.Max(maximum[1] - minimum[1], maximum[2] - minimum[2]));
            double tolerance = Math.Max(modelSpan * 1e-6, 1e-9);
            double[] depths = section.SectionPointsModel
                .Select(point => Dot(point, basis.ViewDirection)).ToArray();
            if (depths.Max() - depths.Min() > tolerance)
                return Fail("VIEW_PLAN_HALF_SECTION_VIEW_PLANE_INVALID", pointer,
                    "Half-section cutting points must lie in one plane parallel to the " +
                    "parent view plane.", out error);
            projectedPoints = section.SectionPointsModel.Select(point => new[]
            {
                Dot(point, basis.Horizontal), Dot(point, basis.Vertical)
            }).ToList();
            double[] first = Subtract2(projectedPoints[0], projectedPoints[1]);
            double[] second = Subtract2(projectedPoints[2], projectedPoints[1]);
            double firstLength = Length2(first);
            double secondLength = Length2(second);
            if (firstLength <= tolerance || secondLength <= tolerance)
                return Fail("VIEW_PLAN_HALF_SECTION_PROJECTED_SEGMENT_INVALID", pointer,
                    "Both half-section segments must remain finite in the parent view.",
                    out error);
            if (Math.Abs(Dot2(first, second) / (firstLength * secondLength)) > 1e-6)
                return Fail("VIEW_PLAN_HALF_SECTION_PROJECTED_ANGLE_INVALID", pointer,
                    "Half-section segments must remain perpendicular in the parent view.",
                    out error);
            double[] centerModel = Enumerable.Range(0, 3)
                .Select(index => (minimum[index] + maximum[index]) / 2.0).ToArray();
            double[] center = { Dot(centerModel, basis.Horizontal),
                Dot(centerModel, basis.Vertical) };
            if (Length2(Subtract2(projectedPoints[1], center)) > tolerance)
                return Fail("VIEW_PLAN_HALF_SECTION_CENTER_INVALID", pointer + "/1",
                    "The half-section bend point must coincide with the frozen part-box " +
                    "center in the parent view.", out error);
            var projectedCorners = BoxCorners(minimum, maximum).Select(point => new[]
            {
                Dot(point, basis.Horizontal), Dot(point, basis.Vertical)
            }).ToList();
            foreach (Tuple<int, double[]> endpoint in new[]
            {
                Tuple.Create(0, first), Tuple.Create(2, second)
            })
            {
                double length = Length2(endpoint.Item2);
                double[] unit = { endpoint.Item2[0] / length, endpoint.Item2[1] / length };
                double required = projectedCorners.Max(corner =>
                    Dot2(Subtract2(corner, center), unit));
                if (length + tolerance < required)
                    return Fail("VIEW_PLAN_HALF_SECTION_OUTLINE_SPAN_INVALID",
                        pointer + "/" + endpoint.Item1,
                        "Each half-section leg must extend from the projected center through " +
                        "the frozen part outline.", out error);
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

        private static bool TryProjectionBasis(ViewPlanBasicViewSpec view,
            out ProjectionBasis result)
        {
            result = null;
            if (view == null) return false;
            double[] direction;
            double[] vertical;
            if (view.OrientationKind == "explicit_basis")
            {
                direction = Unit(view.ViewDirectionModel);
                vertical = Unit(view.UpDirectionModel);
            }
            else if (view.OrientationKind == "standard_model_view")
            {
                switch (view.OrientationName)
                {
                    case "front": direction = new[] { 0.0, 0.0, -1.0 };
                        vertical = new[] { 0.0, 1.0, 0.0 }; break;
                    case "back": direction = new[] { 0.0, 0.0, 1.0 };
                        vertical = new[] { 0.0, 1.0, 0.0 }; break;
                    case "left": direction = new[] { 1.0, 0.0, 0.0 };
                        vertical = new[] { 0.0, 1.0, 0.0 }; break;
                    case "right": direction = new[] { -1.0, 0.0, 0.0 };
                        vertical = new[] { 0.0, 1.0, 0.0 }; break;
                    case "top": direction = new[] { 0.0, -1.0, 0.0 };
                        vertical = new[] { 0.0, 0.0, 1.0 }; break;
                    case "bottom": direction = new[] { 0.0, 1.0, 0.0 };
                        vertical = new[] { 0.0, 0.0, -1.0 }; break;
                    default: return false;
                }
            }
            else return false;
            double[] horizontal = Unit(Cross(vertical, direction));
            if (direction == null || vertical == null || horizontal == null) return false;
            if (Math.Abs(view.RollAngleRad) > 1e-12)
            {
                double cosine = Math.Cos(view.RollAngleRad);
                double sine = Math.Sin(view.RollAngleRad);
                double[] rotatedHorizontal = Enumerable.Range(0, 3)
                    .Select(index => cosine * horizontal[index] + sine * vertical[index])
                    .ToArray();
                vertical = Enumerable.Range(0, 3)
                    .Select(index => -sine * horizontal[index] + cosine * vertical[index])
                    .ToArray();
                horizontal = rotatedHorizontal;
            }
            result = new ProjectionBasis
            {
                ViewDirection = direction,
                Horizontal = horizontal,
                Vertical = vertical
            };
            return true;
        }

        private static bool TryPartBox(JToken geometry, out double[] minimum,
            out double[] maximum)
        {
            minimum = null;
            maximum = null;
            JObject box = geometry["part_box_m"] as JObject;
            if (box == null) return false;
            minimum = new[] { Value(box, "x_min_m"), Value(box, "y_min_m"),
                Value(box, "z_min_m") };
            maximum = new[] { Value(box, "x_max_m"), Value(box, "y_max_m"),
                Value(box, "z_max_m") };
            for (int index = 0; index < 3; index++)
                if (!Finite(minimum[index]) || !Finite(maximum[index]) ||
                    maximum[index] <= minimum[index]) return false;
            return true;
        }

        private static IEnumerable<double[]> BoxCorners(double[] minimum, double[] maximum)
        {
            foreach (double x in new[] { minimum[0], maximum[0] })
                foreach (double y in new[] { minimum[1], maximum[1] })
                    foreach (double z in new[] { minimum[2], maximum[2] })
                        yield return new[] { x, y, z };
        }

        private static bool ProjectedAxisIntersectsSegments(double[] origin,
            double[] direction, ProjectionBasis projection, IList<double[]> points)
        {
            double[] projectedOrigin = { Dot(origin, projection.Horizontal),
                Dot(origin, projection.Vertical) };
            double[] projectedDirection = { Dot(direction, projection.Horizontal),
                Dot(direction, projection.Vertical) };
            for (int index = 0; index + 1 < points.Count; index++)
            {
                if (Length2(projectedDirection) <= 1e-12)
                {
                    if (PointOnSegment2(projectedOrigin, points[index], points[index + 1]))
                        return true;
                }
                else if (LineIntersectsSegment2(projectedOrigin, projectedDirection,
                    points[index], points[index + 1])) return true;
            }
            return false;
        }

        private static bool PointOnSegment2(double[] point, double[] first, double[] second)
        {
            double[] segment = Subtract2(second, first);
            double lengthSquared = Dot2(segment, segment);
            if (lengthSquared <= 1e-24) return false;
            double parameter = Dot2(Subtract2(point, first), segment) / lengthSquared;
            if (parameter < -1e-8 || parameter > 1.0 + 1e-8) return false;
            double[] closest = { first[0] + parameter * segment[0],
                first[1] + parameter * segment[1] };
            return Length2(Subtract2(point, closest)) <=
                Math.Max(Math.Sqrt(lengthSquared) * 1e-6, 1e-9);
        }

        private static bool LineIntersectsSegment2(double[] origin, double[] direction,
            double[] first, double[] second)
        {
            double[] segment = Subtract2(second, first);
            double determinant = Cross2(direction, segment);
            double tolerance = Math.Max(Length2(direction), Length2(segment)) * 1e-8;
            if (Math.Abs(determinant) <= tolerance)
                return Math.Abs(Cross2(Subtract2(first, origin), direction)) <= tolerance;
            double parameter = Cross2(Subtract2(origin, first), direction) / determinant;
            return parameter >= -1e-8 && parameter <= 1.0 + 1e-8;
        }

        private static double Value(JObject source, string name)
        {
            JToken token = source[name];
            return token != null && (token.Type == JTokenType.Integer ||
                token.Type == JTokenType.Float) ? token.Value<double>() : double.NaN;
        }

        private static bool Finite(double value)
        {
            return !double.IsNaN(value) && !double.IsInfinity(value);
        }

        private static double Dot(double[] left, double[] right)
        {
            return left[0] * right[0] + left[1] * right[1] + left[2] * right[2];
        }

        private static double[] Cross(double[] left, double[] right)
        {
            if (left == null || right == null) return null;
            return new[] { left[1] * right[2] - left[2] * right[1],
                left[2] * right[0] - left[0] * right[2],
                left[0] * right[1] - left[1] * right[0] };
        }

        private static double[] Unit(double[] value)
        {
            if (value == null || value.Length != 3 || value.Any(item => !Finite(item)))
                return null;
            double length = Math.Sqrt(Dot(value, value));
            return length > 1e-12 ? value.Select(item => item / length).ToArray() : null;
        }

        private static double[] Subtract2(double[] left, double[] right)
        {
            return new[] { left[0] - right[0], left[1] - right[1] };
        }

        private static double Dot2(double[] left, double[] right)
        {
            return left[0] * right[0] + left[1] * right[1];
        }

        private static double Length2(double[] value)
        {
            return Math.Sqrt(Dot2(value, value));
        }

        private static double Cross2(double[] left, double[] right)
        {
            return left[0] * right[1] - left[1] * right[0];
        }

        private sealed class ProjectionBasis
        {
            public double[] ViewDirection { get; set; }
            public double[] Horizontal { get; set; }
            public double[] Vertical { get; set; }
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
