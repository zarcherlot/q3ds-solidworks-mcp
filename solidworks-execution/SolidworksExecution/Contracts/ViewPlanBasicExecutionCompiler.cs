using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;

namespace SolidworksExecution.Contracts
{
    /// <summary>
    /// Compiles a schema-valid ViewPlan 1.4 document into the native executable subset. This boundary
    /// is COM-free and fail-closed: the complete plan is checked before the first drawing mutation.
    /// It never converts the plan to the legacy DrawingPlan 1.0 contract.
    /// </summary>
    public sealed class ViewPlanBasicExecutionCompiler
    {
        private const double GeometryTolerance = 1e-9;
        private static readonly HashSet<string> SupportedViewTypes =
            new HashSet<string>(new[]
            {
                "model_view", "projected_view", "full_section", "half_section",
                "offset_section", "aligned_section", "removed_section",
                "broken_out_section", "detail_view", "auxiliary_view"
            }, StringComparer.Ordinal);

        public bool TryCompile(ViewPlanDocument document, out ViewPlanBasicExecutionPlan result,
            out ViewPlanExecutionContractError error)
        {
            result = null;
            error = null;
            if (document == null || document.CanonicalPlan == null)
                return Fail("VIEW_PLAN_EXECUTION_CONTRACT_INVALID", "",
                    "A parsed ViewPlan document is required.", out error);

            JObject root = document.CanonicalPlan;
            var rawViews = root["views"] as JArray;
            if (rawViews == null || rawViews.Count == 0)
                return Fail("VIEW_PLAN_EXECUTION_CONTRACT_INVALID", "/views",
                    "At least one view is required.", out error);

            var policy = root["execution_policy"] as JObject;
            var sheet = root["sheet"] as JObject;
            var sheetScale = root["sheet_scale"] as JObject;
            if (policy == null || sheet == null || sheetScale == null)
                return Fail("VIEW_PLAN_EXECUTION_CONTRACT_INVALID", "",
                    "execution_policy, sheet, and sheet_scale are required.", out error);
            double sheetWidth;
            double sheetHeight;
            if (!TryFinitePositive(sheet["width_m"], out sheetWidth) ||
                !TryFinitePositive(sheet["height_m"], out sheetHeight))
                return Fail("VIEW_PLAN_EXECUTION_NUMERIC_INVALID", "/sheet",
                    "Sheet dimensions must be finite and positive.", out error);

            var parsed = new List<ViewPlanBasicViewSpec>();
            var byId = new Dictionary<string, ViewPlanBasicViewSpec>(StringComparer.Ordinal);
            for (int index = 0; index < rawViews.Count; index++)
            {
                var view = rawViews[index] as JObject;
                if (view == null)
                    return Fail("VIEW_PLAN_EXECUTION_CONTRACT_INVALID", Pointer(index),
                        "View must be an object.", out error);

                string type = view.Value<string>("type");
                if (!SupportedViewTypes.Contains(type ?? ""))
                    return Fail("VIEW_PLAN_CAPABILITY_UNSUPPORTED", Pointer(index, "type"),
                        "The native executor does not support view type '" + type + "'.",
                        out error);
                string id = view.Value<string>("id");
                if (string.IsNullOrEmpty(id) || byId.ContainsKey(id))
                    return Fail("VIEW_PLAN_EXECUTION_GRAPH_INVALID", Pointer(index, "id"),
                        "View IDs must be non-empty and unique.", out error);

                var position = view["position_sheet_m"] as JArray;
                double x;
                double y;
                double scale;
                if (!TryFiniteTuple(position, 2, out x, out y) ||
                    !TryFinitePositive(view["scale"], out scale))
                    return Fail("VIEW_PLAN_EXECUTION_NUMERIC_INVALID", Pointer(index),
                        "View position and scale must contain finite values and scale must be positive.",
                        out error);
                double placementXMin;
                double placementYMin;
                double placementXMax;
                double placementYMax;
                if (!TryFiniteRect(view["placement_box"] as JObject, out placementXMin,
                    out placementYMin, out placementXMax, out placementYMax) ||
                    placementXMin < -GeometryTolerance ||
                    placementYMin < -GeometryTolerance ||
                    placementXMax > sheetWidth + GeometryTolerance ||
                    placementYMax > sheetHeight + GeometryTolerance ||
                    x < placementXMin - GeometryTolerance ||
                    x > placementXMax + GeometryTolerance ||
                    y < placementYMin - GeometryTolerance ||
                    y > placementYMax + GeometryTolerance)
                    return Fail("VIEW_PLAN_EXECUTION_LAYOUT_INVALID",
                        Pointer(index, "placement_box"),
                        "placement_box must be a finite positive-area rectangle inside the sheet " +
                        "and contain position_sheet_m.", out error);

                var display = view["display_style"] as JObject;
                if (display == null)
                    return Fail("VIEW_PLAN_EXECUTION_CONTRACT_INVALID",
                        Pointer(index, "display_style"), "display_style is required.", out error);
                string displayMode = display.Value<string>("mode");
                bool faceted = display.Value<bool>("faceted");
                bool edges = display.Value<bool>("edges");
                string hiddenLines = view.Value<string>("hidden_lines");
                string tangentEdges = view.Value<string>("tangent_edges");
                if (!TryValidateDisplayContract(displayMode, faceted, edges, hiddenLines,
                    out string displayError))
                    return Fail("VIEW_PLAN_DISPLAY_CONTRACT_UNSUPPORTED",
                        Pointer(index, "display_style"), displayError, out error);

                var spec = new ViewPlanBasicViewSpec
                {
                    OriginalIndex = index,
                    Id = id,
                    Type = type,
                    ParentId = view.Value<string>("parent_view_id"),
                    Alignment = view.Value<string>("alignment"),
                    X = x,
                    Y = y,
                    Scale = scale,
                    PlacementXMin = placementXMin,
                    PlacementYMin = placementYMin,
                    PlacementXMax = placementXMax,
                    PlacementYMax = placementYMax,
                    DisplayMode = displayMode,
                    Faceted = faceted,
                    Edges = edges,
                    HiddenLines = hiddenLines,
                    TangentEdges = tangentEdges
                };

                var orientation = view["orientation"] as JObject;
                if (orientation == null)
                    return Fail("VIEW_PLAN_EXECUTION_CONTRACT_INVALID",
                        Pointer(index, "orientation"), "orientation is required.", out error);
                spec.OrientationKind = orientation.Value<string>("kind");
                if (IsModelOrientationType(type))
                {
                    if (!TryCompileModelOrientation(spec, orientation, policy, index, out error))
                        return false;
                    if (type == "broken_out_section" &&
                        !TryCompileBrokenOut(spec, view, index, out error)) return false;
                }
                else
                {
                    if (spec.OrientationKind != "derived_from_parent")
                        return Fail("VIEW_PLAN_EXECUTION_GRAPH_INVALID",
                            Pointer(index, "orientation", "kind"),
                            "A parent-derived view must use derived_from_parent orientation.", out error);
                    var source = view["source"] as JObject;
                    spec.ProjectionDirection = source != null
                        ? source.Value<string>("projection_direction") : null;
                    string sourceParent = source != null ? source.Value<string>("reference") : null;
                    if (string.IsNullOrEmpty(spec.ParentId) || spec.ParentId != sourceParent)
                        return Fail("VIEW_PLAN_EXECUTION_GRAPH_INVALID",
                            Pointer(index, "parent_view_id"),
                            "parent_view_id must equal source.reference.", out error);
                    if (IsSectionType(type) &&
                        !TryCompileSection(spec, view, index, out error)) return false;
                    if (type == "detail_view" &&
                        !TryCompileDetail(spec, view, index, out error)) return false;
                    if (type == "auxiliary_view" &&
                        !TryCompileAuxiliary(spec, view, index, out error)) return false;
                }
                if (!TryCompileCenterElements(spec, view, index, out error)) return false;

                parsed.Add(spec);
                byId.Add(id, spec);
            }

            bool[] defaultCenterMarkLineValues = parsed
                .SelectMany(item => item.CenterMarks)
                .Where(item => item.UseDocumentDefaults)
                .Select(item => item.ShowLines).Distinct().ToArray();
            if (defaultCenterMarkLineValues.Length > 1)
                return Fail("VIEW_PLAN_CENTER_MARK_DEFAULT_CONFLICT", "/views",
                    "All center marks using document defaults must request one show_lines value.",
                    out error);

            string mainId = root.Value<string>("main_view_id");
            if (!byId.TryGetValue(mainId ?? "", out ViewPlanBasicViewSpec main) ||
                main.Type != "model_view")
                return Fail("VIEW_PLAN_EXECUTION_GRAPH_INVALID", "/main_view_id",
                    "main_view_id must reference a model_view in this plan.", out error);

            string projectionMethod = root.Value<string>("projection_method");
            foreach (ViewPlanBasicViewSpec spec in parsed.Where(item => item.ParentId != null))
            {
                if (!byId.TryGetValue(spec.ParentId, out ViewPlanBasicViewSpec parent))
                    return Fail("VIEW_PLAN_EXECUTION_GRAPH_INVALID",
                        Pointer(spec.OriginalIndex, "parent_view_id"),
                        "Parent view does not exist: '" + spec.ParentId + "'.", out error);
                if (spec.Type == "projected_view" &&
                    !TryValidateProjectedGeometry(spec, parent, projectionMethod,
                        out string message))
                    return Fail("VIEW_PLAN_PROJECTION_CONTRACT_INVALID",
                        Pointer(spec.OriginalIndex, "position_sheet_m"), message, out error);
                if (IsSectionType(spec.Type) &&
                    !TryValidateSectionPlacement(spec, parent, out string sectionMessage))
                    return Fail("VIEW_PLAN_SECTION_PLACEMENT_INVALID",
                        Pointer(spec.OriginalIndex, "position_sheet_m"), sectionMessage, out error);
                if (spec.Type == "detail_view" &&
                    !TryValidateDetailProfile(spec, parent, sheetWidth, sheetHeight,
                        out string detailMessage))
                    return Fail("VIEW_PLAN_DETAIL_PROFILE_INVALID",
                        Pointer(spec.OriginalIndex, "detail_definition"), detailMessage, out error);
                if (spec.Type == "auxiliary_view" &&
                    !TryValidateAuxiliaryPlacement(spec, sheetWidth, sheetHeight,
                        out string auxiliaryMessage))
                    return Fail("VIEW_PLAN_AUXILIARY_PLACEMENT_INVALID",
                        Pointer(spec.OriginalIndex), auxiliaryMessage, out error);
            }

            foreach (ViewPlanBasicViewSpec spec in parsed.Where(item =>
                item.Type == "broken_out_section"))
                if (!TryValidateCircleInsidePlacement(spec.X + spec.ProfileOffsetX,
                    spec.Y + spec.ProfileOffsetY, spec.ProfileRadiusSheet,
                    spec, sheetWidth, sheetHeight, out string brokenMessage))
                    return Fail("VIEW_PLAN_BROKEN_OUT_PROFILE_INVALID",
                        Pointer(spec.OriginalIndex, "broken_out_definition"), brokenMessage,
                        out error);

            List<ViewPlanBasicViewSpec> ordered;
            if (!TryTopologicalOrder(parsed, byId, out ordered))
                return Fail("VIEW_PLAN_EXECUTION_GRAPH_INVALID", "/views",
                    "The view parent graph contains a cycle.", out error);

            result = new ViewPlanBasicExecutionPlan
            {
                PlanId = document.PlanId,
                PlanCanonicalSha256 = document.CanonicalSha256,
                ModelPath = root.Value<string>("model_path"),
                ModelSha256 = root.Value<string>("model_sha256"),
                DrawingPath = root.Value<string>("drawing_path"),
                DrawingSha256 = root.Value<string>("drawing_sha256"),
                Configuration = root.Value<string>("configuration"),
                DisplayState = root.Value<string>("display_state"),
                ProjectionMethod = projectionMethod,
                SheetName = sheet.Value<string>("name"),
                SheetWidth = sheetWidth,
                SheetHeight = sheetHeight,
                SheetScaleNumerator = sheetScale.Value<int>("numerator"),
                SheetScaleDenominator = sheetScale.Value<int>("denominator"),
                TransientModelViewPolicy = policy.Value<string>("transient_model_view_policy"),
                Views = ordered,
                InputArtifacts = CompileInputArtifacts(root)
            };
            return true;
        }

        private static IList<ViewPlanBoundArtifact> CompileInputArtifacts(JObject root)
        {
            var artifacts = new List<ViewPlanBoundArtifact>
            {
                NewArtifact("model", root.Value<string>("model_path"),
                    root.Value<string>("model_sha256")),
                NewArtifact("drawing", root.Value<string>("drawing_path"),
                    root.Value<string>("drawing_sha256")),
                NewArtifact("geometry_report", root.Value<string>("geometry_report_path"),
                    root.Value<string>("geometry_report_sha256")),
                NewArtifact("readiness_report", root.Value<string>("readiness_report_path"),
                    root.Value<string>("readiness_report_sha256"))
            };
            var images = (JArray)root["standard_view_images"];
            foreach (JObject image in images.OfType<JObject>())
                artifacts.Add(NewArtifact("standard_view_image:" + image.Value<string>("view"),
                    image.Value<string>("path"), image.Value<string>("sha256")));
            return artifacts;
        }

        private static ViewPlanBoundArtifact NewArtifact(string role, string path, string sha256)
        {
            return new ViewPlanBoundArtifact { Role = role, Path = path, Sha256 = sha256 };
        }

        private static bool TryCompileModelOrientation(ViewPlanBasicViewSpec spec,
            JObject orientation, JObject policy, int index,
            out ViewPlanExecutionContractError error)
        {
            error = null;
            double rollAngle;
            if (!TryFinite(orientation["roll_angle_rad"], out rollAngle))
                return Fail("VIEW_PLAN_EXECUTION_NUMERIC_INVALID",
                    Pointer(index, "orientation", "roll_angle_rad"),
                    "roll_angle_rad must be finite.", out error);
            spec.RollAngleRad = rollAngle;
            if (spec.OrientationKind == "standard_model_view")
            {
                spec.OrientationName = orientation.Value<string>("standard_view");
                return true;
            }
            if (spec.OrientationKind == "named_model_view")
            {
                spec.OrientationName = orientation.Value<string>("name_exact");
                return !string.IsNullOrWhiteSpace(spec.OrientationName) ||
                    Fail("VIEW_PLAN_EXECUTION_CONTRACT_INVALID",
                        Pointer(index, "orientation", "name_exact"),
                        "Named orientation must have an exact non-empty name.", out error);
            }
            if (spec.OrientationKind == "explicit_basis")
            {
                if (policy.Value<string>("transient_model_view_policy") != "allow_in_memory_restore")
                    return Fail("VIEW_PLAN_TRANSIENT_ORIENTATION_FORBIDDEN",
                        Pointer(index, "orientation"),
                        "explicit_basis requires transient_model_view_policy=allow_in_memory_restore.",
                        out error);
                double[] direction;
                double[] up;
                if (!TryFiniteVector3(orientation["view_direction_model"] as JArray,
                    out direction) || !TryFiniteVector3(
                        orientation["up_direction_model"] as JArray, out up))
                    return Fail("VIEW_PLAN_EXECUTION_NUMERIC_INVALID",
                        Pointer(index, "orientation"),
                        "Explicit basis vectors must contain three finite values.", out error);
                double directionLength = Length(direction);
                double upLength = Length(up);
                if (directionLength <= 1e-12 || upLength <= 1e-12)
                    return Fail("VIEW_PLAN_EXPLICIT_BASIS_INVALID",
                        Pointer(index, "orientation"),
                        "Explicit basis vectors must be non-zero.", out error);
                Normalize(direction, directionLength);
                Normalize(up, upLength);
                if (Math.Abs(Dot(direction, up)) > 1e-6)
                    return Fail("VIEW_PLAN_EXPLICIT_BASIS_INVALID",
                        Pointer(index, "orientation"),
                        "Explicit view and up directions must be orthogonal.", out error);
                // Reconstruct the up vector from the declared plane to produce a numerically
                // orthonormal basis without changing its engineering direction.
                double[] right = Cross(direction, up);
                Normalize(right, Length(right));
                up = Cross(right, direction);
                Normalize(up, Length(up));
                spec.ViewDirectionModel = direction;
                spec.UpDirectionModel = up;
                return true;
            }
            return Fail("VIEW_PLAN_EXECUTION_CONTRACT_INVALID",
                Pointer(index, "orientation", "kind"), "Unsupported model orientation kind.", out error);
        }

        private static bool TryCompileBrokenOut(ViewPlanBasicViewSpec spec, JObject view,
            int index, out ViewPlanExecutionContractError error)
        {
            error = null;
            var section = view["section_definition"] as JObject;
            var definition = view["broken_out_definition"] as JObject;
            if (section == null || definition == null)
                return Fail("VIEW_PLAN_BROKEN_OUT_CONTRACT_INVALID", Pointer(index),
                    "broken_out_section requires section_definition and broken_out_definition.",
                    out error);
            var featureIds = section["feature_ids"] as JArray;
            var points = section["cutting_line_points_model_m"] as JArray;
            double sectionDepth;
            if (section.Value<string>("cutting_plane_mode") != "explicit_broken_out" ||
                featureIds == null || featureIds.Count == 0 || points == null ||
                points.Count != 0 || !IsNull(section["cutting_line_axis"]) ||
                !IsNull(section["line_extension_ratio"]) ||
                section.Value<bool>("reverse_direction") ||
                !TryFinite(section["section_depth_m"], out sectionDepth) ||
                Math.Abs(sectionDepth) > GeometryTolerance)
                return Fail("VIEW_PLAN_BROKEN_OUT_CONTRACT_INVALID",
                    Pointer(index, "section_definition"),
                    "broken_out_section requires explicit_broken_out, feature IDs, no cutting " +
                    "line, reverse_direction=false, and section_depth_m=0.", out error);
            if (definition.Value<string>("base_view_mode") != "model_orientation" ||
                definition.Value<string>("boundary_mode") != "circle")
                return Fail("VIEW_PLAN_BROKEN_OUT_CONTRACT_INVALID",
                    Pointer(index, "broken_out_definition"),
                    "Only a circular boundary on a model-orientation base view is supported.",
                    out error);
            double offsetX;
            double offsetY;
            double radius;
            double depth;
            if (!TryFiniteTuple(definition["center_offset_from_view_m"] as JArray, 2,
                    out offsetX, out offsetY) ||
                !TryFinitePositive(definition["radius_sheet_m"], out radius) ||
                !TryFinitePositive(definition["depth_m"], out depth))
                return Fail("VIEW_PLAN_EXECUTION_NUMERIC_INVALID",
                    Pointer(index, "broken_out_definition"),
                    "Broken-out center, radius, and depth must be finite and positive where " +
                    "required.", out error);
            spec.SectionCuttingPlaneMode = "explicit_broken_out";
            spec.SectionFeatureIds = featureIds.Values<string>().ToList();
            spec.SectionPointsModel = new List<double[]>();
            spec.ProfileOffsetX = offsetX;
            spec.ProfileOffsetY = offsetY;
            spec.ProfileRadiusSheet = radius;
            spec.BrokenOutDepth = depth;
            return true;
        }

        private static bool TryCompileDetail(ViewPlanBasicViewSpec spec, JObject view,
            int index, out ViewPlanExecutionContractError error)
        {
            error = null;
            var definition = view["detail_definition"] as JObject;
            var label = view["label"] as JObject;
            if (definition == null || label == null || !label.Value<bool>("show") ||
                string.IsNullOrWhiteSpace(label.Value<string>("text")))
                return Fail("VIEW_PLAN_DETAIL_CONTRACT_INVALID", Pointer(index),
                    "detail_view requires detail_definition and a visible non-empty label.",
                    out error);
            if (spec.Alignment != "none" || definition.Value<string>("profile_mode") != "circle")
                return Fail("VIEW_PLAN_DETAIL_CONTRACT_INVALID", Pointer(index),
                    "detail_view requires alignment=none and profile_mode=circle.", out error);
            double offsetX;
            double offsetY;
            double radius;
            if (!TryFiniteTuple(definition["center_offset_from_parent_m"] as JArray, 2,
                    out offsetX, out offsetY) ||
                !TryFinitePositive(definition["radius_sheet_m"], out radius))
                return Fail("VIEW_PLAN_EXECUTION_NUMERIC_INVALID",
                    Pointer(index, "detail_definition"),
                    "Detail profile center and radius must be finite and radius must be positive.",
                    out error);
            int style;
            int showType;
            string styleName = definition.Value<string>("style");
            string showTypeName = definition.Value<string>("show_type");
            if (!TryMapDetailStyle(styleName, out style) ||
                !TryMapDetailShowType(showTypeName, out showType))
                return Fail("VIEW_PLAN_DETAIL_CONTRACT_INVALID",
                    Pointer(index, "detail_definition"),
                    "Detail style or show_type is not supported by the native contract.", out error);
            bool fullOutline = definition.Value<bool>("full_outline");
            bool jaggedOutline = definition.Value<bool>("jagged_outline");
            bool noOutline = definition.Value<bool>("no_outline");
            if (noOutline && (fullOutline || jaggedOutline))
                return Fail("VIEW_PLAN_DETAIL_CONTRACT_INVALID",
                    Pointer(index, "detail_definition"),
                    "no_outline=true requires full_outline=false and jagged_outline=false.",
                    out error);
            int shapeIntensity = definition.Value<int>("shape_intensity");
            if (shapeIntensity < 1 || shapeIntensity > 5)
                return Fail("VIEW_PLAN_DETAIL_CONTRACT_INVALID",
                    Pointer(index, "detail_definition", "shape_intensity"),
                    "shape_intensity must be in [1, 5].", out error);

            spec.ProfileOffsetX = offsetX;
            spec.ProfileOffsetY = offsetY;
            spec.ProfileRadiusSheet = radius;
            spec.DetailStyleName = styleName;
            spec.DetailStyle = style;
            spec.DetailShowTypeName = showTypeName;
            spec.DetailShowType = showType;
            spec.DetailFullOutline = fullOutline;
            spec.DetailJaggedOutline = jaggedOutline;
            spec.DetailNoOutline = noOutline;
            spec.DetailShapeIntensity = shapeIntensity;
            spec.DetailLabel = label.Value<string>("text");
            spec.DetailLabelPositionMode = label.Value<string>("position_mode");
            if (spec.DetailLabelPositionMode == "explicit")
            {
                double labelX;
                double labelY;
                if (!TryFiniteTuple(label["position_sheet_m"] as JArray, 2,
                    out labelX, out labelY))
                    return Fail("VIEW_PLAN_EXECUTION_NUMERIC_INVALID",
                        Pointer(index, "label", "position_sheet_m"),
                        "Explicit detail label position must contain two finite values.", out error);
                spec.DetailLabelX = labelX;
                spec.DetailLabelY = labelY;
            }
            else if (spec.DetailLabelPositionMode != "document_default")
                return Fail("VIEW_PLAN_DETAIL_CONTRACT_INVALID",
                    Pointer(index, "label", "position_mode"),
                    "Unsupported detail label position mode.", out error);
            return true;
        }

        private static bool TryMapDetailStyle(string value, out int result)
        {
            result = value == "standard" ? 0 : value == "broken" ? 1 :
                value == "leader" ? 2 : value == "no_leader" ? 3 :
                value == "connected" ? 4 : -1;
            return result >= 0;
        }

        private static bool TryMapDetailShowType(string value, out int result)
        {
            result = value == "profile" ? 0 : value == "circle" ? 1 :
                value == "none" ? 2 : -1;
            return result >= 0;
        }

        private static bool TryCompileAuxiliary(ViewPlanBasicViewSpec spec, JObject view,
            int index, out ViewPlanExecutionContractError error)
        {
            error = null;
            var definition = view["auxiliary_definition"] as JObject;
            var label = view["label"] as JObject;
            if (definition == null || label == null || !label.Value<bool>("show") ||
                string.IsNullOrWhiteSpace(label.Value<string>("text")))
                return Fail("VIEW_PLAN_AUXILIARY_CONTRACT_INVALID", Pointer(index),
                    "auxiliary_view requires auxiliary_definition and a visible non-empty label.",
                    out error);

            double[] start;
            double[] end;
            double tolerance;
            if (!TryFiniteVector3(definition["reference_edge_start_model_m"] as JArray,
                    out start) ||
                !TryFiniteVector3(definition["reference_edge_end_model_m"] as JArray,
                    out end) ||
                !TryFinitePositive(definition["match_tolerance_sheet_m"], out tolerance))
                return Fail("VIEW_PLAN_EXECUTION_NUMERIC_INVALID",
                    Pointer(index, "auxiliary_definition"),
                    "Auxiliary reference-edge endpoints and match tolerance must be finite; " +
                    "the tolerance must be positive.", out error);
            if (Distance(start, end) <= GeometryTolerance)
                return Fail("VIEW_PLAN_AUXILIARY_REFERENCE_EDGE_INVALID",
                    Pointer(index, "auxiliary_definition"),
                    "Auxiliary reference-edge endpoints must be distinct.", out error);
            if (tolerance > 0.005 + GeometryTolerance)
                return Fail("VIEW_PLAN_AUXILIARY_REFERENCE_EDGE_INVALID",
                    Pointer(index, "auxiliary_definition", "match_tolerance_sheet_m"),
                    "Auxiliary match tolerance cannot exceed 0.005 m.", out error);

            bool notAligned = definition.Value<bool>("not_aligned");
            string requiredAlignment = notAligned ? "not_aligned" : "projected";
            if (!string.Equals(spec.Alignment, requiredAlignment, StringComparison.Ordinal))
                return Fail("VIEW_PLAN_AUXILIARY_ALIGNMENT_INVALID",
                    Pointer(index, "alignment"),
                    "alignment must be '" + requiredAlignment +
                    "' when auxiliary_definition.not_aligned=" +
                    notAligned.ToString().ToLowerInvariant() + ".", out error);

            spec.AuxiliaryReferenceEdgeStartModel = start;
            spec.AuxiliaryReferenceEdgeEndModel = end;
            spec.AuxiliaryMatchToleranceSheet = tolerance;
            spec.AuxiliaryNotAligned = notAligned;
            spec.AuxiliaryShowArrow = definition.Value<bool>("show_arrow");
            spec.AuxiliaryFlip = definition.Value<bool>("flip");
            if (!spec.AuxiliaryShowArrow)
                return Fail("VIEW_PLAN_CAPABILITY_UNSUPPORTED",
                    Pointer(index, "auxiliary_definition", "show_arrow"),
                    "SolidWorks 2025 does not persist show_arrow=false for native auxiliary " +
                    "views; hidden auxiliary arrows are not executable.", out error);
            spec.AuxiliaryLabel = label.Value<string>("text");
            spec.AuxiliaryLabelPositionMode = label.Value<string>("position_mode");
            if (spec.AuxiliaryLabelPositionMode == "explicit")
            {
                double labelX;
                double labelY;
                if (!TryFiniteTuple(label["position_sheet_m"] as JArray, 2,
                    out labelX, out labelY))
                    return Fail("VIEW_PLAN_EXECUTION_NUMERIC_INVALID",
                        Pointer(index, "label", "position_sheet_m"),
                        "Explicit auxiliary label position must contain two finite values.",
                        out error);
                spec.AuxiliaryLabelX = labelX;
                spec.AuxiliaryLabelY = labelY;
            }
            else if (spec.AuxiliaryLabelPositionMode != "document_default")
                return Fail("VIEW_PLAN_AUXILIARY_CONTRACT_INVALID",
                    Pointer(index, "label", "position_mode"),
                    "Unsupported auxiliary label position mode.", out error);
            return true;
        }

        private static bool TryCompileCenterElements(ViewPlanBasicViewSpec spec, JObject view,
            int viewIndex, out ViewPlanExecutionContractError error)
        {
            error = null;
            spec.CenterMarks = new List<ViewPlanCenterMarkSpec>();
            spec.SymmetryCenterlines = new List<ViewPlanSymmetryCenterlineSpec>();
            var marks = view["center_marks"] as JArray;
            if (marks == null)
                return Fail("VIEW_PLAN_CENTER_MARK_CONTRACT_INVALID",
                    Pointer(viewIndex, "center_marks"),
                    "center_marks must be an array.", out error);
            for (int markIndex = 0; markIndex < marks.Count; markIndex++)
            {
                var mark = marks[markIndex] as JObject;
                string basePointer = Pointer(viewIndex, "center_marks", markIndex.ToString());
                var featureIds = mark != null ? mark["feature_ids"] as JArray : null;
                int expectedCount = mark != null ? mark.Value<int>("expected_count") : 0;
                string styleName = mark != null ? mark.Value<string>("style") : null;
                int style = styleName == "single" ? 2 : styleName == "linear_group" ? 3 :
                    styleName == "circular_group" ? 4 : -1;
                int color;
                if (mark == null || string.IsNullOrWhiteSpace(mark.Value<string>("id")) ||
                    featureIds == null || featureIds.Count == 0 ||
                    featureIds.Any(item => item.Type != JTokenType.String ||
                        string.IsNullOrWhiteSpace(item.Value<string>())) ||
                    mark.Value<string>("selection_strategy") !=
                        "visible_closed_circular_edges_by_feature" ||
                    mark.Value<string>("deduplicate_by") != "projected_center" ||
                    expectedCount < 1 || style < 0 ||
                    !TryRgb(mark["color_rgb"] as JArray, out color))
                    return Fail("VIEW_PLAN_CENTER_MARK_CONTRACT_INVALID", basePointer,
                        "Center marks require feature-bound circular-edge selection, a positive " +
                        "expected_count, a supported style, and one valid RGB color.", out error);
                bool showLines = mark.Value<bool>("show_lines");
                if (style == 4 && !showLines)
                    return Fail("VIEW_PLAN_CENTER_MARK_CONTRACT_INVALID",
                        basePointer + "/show_lines",
                        "circular_group center marks require show_lines=true.", out error);
                spec.CenterMarks.Add(new ViewPlanCenterMarkSpec
                {
                    Id = mark.Value<string>("id"),
                    OriginalIndex = markIndex,
                    FeatureIds = featureIds.Values<string>().ToList(),
                    ExpectedCount = expectedCount,
                    StyleName = styleName,
                    Style = style,
                    UseDocumentDefaults = mark.Value<bool>("use_document_defaults"),
                    ShowLines = showLines,
                    Propagate = mark.Value<bool>("propagate"),
                    Slot = mark.Value<bool>("slot"),
                    Color = color
                });
            }

            var lines = view["symmetry_centerlines"] as JArray;
            if (lines == null)
                return Fail("VIEW_PLAN_CENTERLINE_CONTRACT_INVALID",
                    Pointer(viewIndex, "symmetry_centerlines"),
                    "symmetry_centerlines must be an array.", out error);
            for (int lineIndex = 0; lineIndex < lines.Count; lineIndex++)
            {
                var line = lines[lineIndex] as JObject;
                string basePointer = Pointer(viewIndex, "symmetry_centerlines",
                    lineIndex.ToString());
                double ratio;
                int color;
                string axis = line != null ? line.Value<string>("axis") : null;
                if (line == null || string.IsNullOrWhiteSpace(line.Value<string>("id")) ||
                    (axis != "horizontal" && axis != "vertical") ||
                    line.Value<string>("selection_strategy") !=
                        "opposed_visible_linear_edges" ||
                    !TryFinite(line["minimum_edge_span_ratio"], out ratio) ||
                    ratio <= 0.0 || ratio > 1.0 ||
                    !TryRgb(line["color_rgb"] as JArray, out color))
                    return Fail("VIEW_PLAN_CENTERLINE_CONTRACT_INVALID", basePointer,
                        "Symmetry centerlines require one sheet axis, opposed visible linear " +
                        "edges, a ratio in (0, 1], and one valid RGB color.", out error);
                spec.SymmetryCenterlines.Add(new ViewPlanSymmetryCenterlineSpec
                {
                    Id = line.Value<string>("id"),
                    OriginalIndex = lineIndex,
                    Axis = axis,
                    MinimumEdgeSpanRatio = ratio,
                    Color = color
                });
            }
            return true;
        }

        private static bool TryRgb(JArray values, out int color)
        {
            color = 0;
            if (values == null || values.Count != 3 || values.Any(item =>
                item.Type != JTokenType.Integer || item.Value<int>() < 0 ||
                item.Value<int>() > 255)) return false;
            color = values[0].Value<int>() | (values[1].Value<int>() << 8) |
                (values[2].Value<int>() << 16);
            return true;
        }

        private static bool TryValidateAuxiliaryPlacement(ViewPlanBasicViewSpec auxiliary,
            double sheetWidth, double sheetHeight, out string message)
        {
            message = null;
            if (auxiliary.AuxiliaryLabelPositionMode == "explicit" &&
                (auxiliary.AuxiliaryLabelX.Value < -GeometryTolerance ||
                 auxiliary.AuxiliaryLabelY.Value < -GeometryTolerance ||
                 auxiliary.AuxiliaryLabelX.Value > sheetWidth + GeometryTolerance ||
                 auxiliary.AuxiliaryLabelY.Value > sheetHeight + GeometryTolerance))
                message = "Explicit auxiliary label position must lie inside the drawing sheet.";
            return message == null;
        }

        private static bool TryValidateDetailProfile(ViewPlanBasicViewSpec detail,
            ViewPlanBasicViewSpec parent, double sheetWidth, double sheetHeight,
            out string message)
        {
            if (!TryValidateCircleInsidePlacement(parent.X + detail.ProfileOffsetX,
                parent.Y + detail.ProfileOffsetY, detail.ProfileRadiusSheet, parent,
                sheetWidth, sheetHeight, out message)) return false;
            if (detail.DetailLabelPositionMode == "explicit" &&
                (detail.DetailLabelX.Value < -GeometryTolerance ||
                 detail.DetailLabelY.Value < -GeometryTolerance ||
                 detail.DetailLabelX.Value > sheetWidth + GeometryTolerance ||
                 detail.DetailLabelY.Value > sheetHeight + GeometryTolerance))
            {
                message = "Explicit detail label position must lie inside the drawing sheet.";
                return false;
            }
            return true;
        }

        private static bool TryValidateCircleInsidePlacement(double centerX, double centerY,
            double radius, ViewPlanBasicViewSpec source, double sheetWidth, double sheetHeight,
            out string message)
        {
            message = null;
            if (centerX - radius < source.PlacementXMin - GeometryTolerance ||
                centerY - radius < source.PlacementYMin - GeometryTolerance ||
                centerX + radius > source.PlacementXMax + GeometryTolerance ||
                centerY + radius > source.PlacementYMax + GeometryTolerance)
                message = "The complete circular profile must lie inside the source view's " +
                    "frozen placement_box.";
            else if (centerX - radius < -GeometryTolerance ||
                centerY - radius < -GeometryTolerance ||
                centerX + radius > sheetWidth + GeometryTolerance ||
                centerY + radius > sheetHeight + GeometryTolerance)
                message = "The complete circular profile must lie inside the drawing sheet.";
            return message == null;
        }

        private static bool TryCompileSection(ViewPlanBasicViewSpec spec, JObject view,
            int index, out ViewPlanExecutionContractError error)
        {
            error = null;
            var section = view["section_definition"] as JObject;
            var label = view["label"] as JObject;
            if (section == null || label == null || label.Value<bool>("show") != true)
                return Fail("VIEW_PLAN_SECTION_CONTRACT_INVALID", Pointer(index),
                    "A section view requires section_definition and a visible label.", out error);
            spec.SectionCuttingPlaneMode = section.Value<string>("cutting_plane_mode");
            spec.SectionCuttingLineAxis = section.Value<string>("cutting_line_axis");
            spec.SectionReverseDirection = section.Value<bool>("reverse_direction");
            spec.SectionLabel = label.Value<string>("text");
            if (string.IsNullOrWhiteSpace(spec.SectionLabel))
                return Fail("VIEW_PLAN_SECTION_CONTRACT_INVALID", Pointer(index, "label", "text"),
                    "Section label text must be non-empty.", out error);
            double depth;
            if (!TryFinite(section["section_depth_m"], out depth) || depth < 0.0)
                return Fail("VIEW_PLAN_EXECUTION_NUMERIC_INVALID",
                    Pointer(index, "section_definition", "section_depth_m"),
                    "section_depth_m must be finite and non-negative.", out error);
            spec.SectionDepth = depth;
            JToken extension = section["line_extension_ratio"];
            if (extension != null && extension.Type != JTokenType.Null)
            {
                double ratio;
                if (!TryFinite(extension, out ratio) || ratio < 0.02 || ratio > 1.0)
                    return Fail("VIEW_PLAN_EXECUTION_NUMERIC_INVALID",
                        Pointer(index, "section_definition", "line_extension_ratio"),
                        "line_extension_ratio must be finite in [0.02, 1.0].", out error);
                spec.SectionLineExtensionRatio = ratio;
            }
            var featureIds = section["feature_ids"] as JArray;
            spec.SectionFeatureIds = featureIds == null
                ? new List<string>()
                : featureIds.Values<string>().ToList();
            var points = section["cutting_line_points_model_m"] as JArray;
            var compiledPoints = new List<double[]>();
            if (points != null)
            {
                for (int pointIndex = 0; pointIndex < points.Count; pointIndex++)
                {
                    double[] point;
                    if (!TryFiniteVector3(points[pointIndex] as JArray, out point))
                        return Fail("VIEW_PLAN_EXECUTION_NUMERIC_INVALID",
                            Pointer(index, "section_definition", "cutting_line_points_model_m") +
                            "/" + pointIndex,
                            "Every cutting-line point must contain three finite values.", out error);
                    compiledPoints.Add(point);
                }
            }
            spec.SectionPointsModel = compiledPoints;

            string expectedMode = spec.Type == "full_section" ? "through_feature_axes" :
                spec.Type == "half_section" ? "explicit_half" :
                spec.Type == "offset_section" ? "explicit_offset" :
                spec.Type == "aligned_section" ? "explicit_aligned" : "explicit_removed";
            if (spec.SectionCuttingPlaneMode != expectedMode)
                return Fail("VIEW_PLAN_SECTION_CONTRACT_INVALID",
                    Pointer(index, "section_definition", "cutting_plane_mode"),
                    "Section type requires cutting_plane_mode='" + expectedMode + "'.", out error);

            if (spec.Type == "full_section")
            {
                if (compiledPoints.Count != 0 || spec.SectionFeatureIds.Count == 0 ||
                    (spec.SectionCuttingLineAxis != "horizontal" &&
                     spec.SectionCuttingLineAxis != "vertical") ||
                    !spec.SectionLineExtensionRatio.HasValue)
                    return Fail("VIEW_PLAN_SECTION_CONTRACT_INVALID",
                        Pointer(index, "section_definition"),
                        "full_section requires feature axes, horizontal/vertical line axis, " +
                        "line extension, and no explicit points.", out error);
                return true;
            }

            int expectedCount = spec.Type == "half_section" ||
                spec.Type == "aligned_section" ? 3 :
                spec.Type == "removed_section" ? 2 : -1;
            if ((expectedCount > 0 && compiledPoints.Count != expectedCount) ||
                (spec.Type == "offset_section" && compiledPoints.Count < 4))
                return Fail("VIEW_PLAN_SECTION_CONTRACT_INVALID",
                    Pointer(index, "section_definition", "cutting_line_points_model_m"),
                    "The explicit cutting-line point count does not match the section type.", out error);
            for (int pointIndex = 0; pointIndex + 1 < compiledPoints.Count; pointIndex++)
                if (Distance(compiledPoints[pointIndex], compiledPoints[pointIndex + 1]) <=
                    GeometryTolerance)
                    return Fail("VIEW_PLAN_SECTION_GEOMETRY_INVALID",
                        Pointer(index, "section_definition", "cutting_line_points_model_m") +
                        "/" + pointIndex,
                        "Consecutive cutting-line points must be distinct.", out error);
            if (spec.Type == "half_section" &&
                Math.Abs(UnitDot(compiledPoints[0], compiledPoints[1], compiledPoints[2])) > 1e-6)
                return Fail("VIEW_PLAN_SECTION_GEOMETRY_INVALID",
                    Pointer(index, "section_definition", "cutting_line_points_model_m"),
                    "half_section cutting segments must be perpendicular.", out error);
            if (spec.Type == "aligned_section" &&
                Math.Abs(UnitDot(compiledPoints[0], compiledPoints[1], compiledPoints[2])) >
                    1.0 - 1e-8)
                return Fail("VIEW_PLAN_SECTION_GEOMETRY_INVALID",
                    Pointer(index, "section_definition", "cutting_line_points_model_m"),
                    "aligned_section cutting segments must form a non-collinear angle.", out error);
            return true;
        }

        private static bool TryValidateSectionPlacement(ViewPlanBasicViewSpec section,
            ViewPlanBasicViewSpec parent, out string message)
        {
            message = null;
            if (section.Type == "removed_section")
            {
                if (section.Alignment != "not_aligned")
                {
                    message = "removed_section must use not_aligned placement.";
                    return false;
                }
                return true;
            }
            if (section.Alignment == "not_aligned") return true;
            if (section.Alignment != "projected")
            {
                message = "A section view must use projected or not_aligned placement.";
                return false;
            }
            if (section.Type == "full_section")
            {
                bool horizontal = section.SectionCuttingLineAxis == "horizontal";
                double alignedDelta = horizontal ? section.X - parent.X : section.Y - parent.Y;
                double projectedDelta = horizontal ? section.Y - parent.Y : section.X - parent.X;
                if (Math.Abs(alignedDelta) > GeometryTolerance ||
                    Math.Abs(projectedDelta) <= GeometryTolerance)
                {
                    message = horizontal
                        ? "A horizontal cutting line requires the projected section to share parent X."
                        : "A vertical cutting line requires the projected section to share parent Y.";
                    return false;
                }
            }
            return true;
        }

        private static bool IsSectionType(string type)
        {
            return type == "full_section" || type == "half_section" ||
                type == "offset_section" || type == "aligned_section" ||
                type == "removed_section";
        }

        private static bool IsModelOrientationType(string type)
        {
            return type == "model_view" || type == "broken_out_section";
        }

        private static double Distance(double[] first, double[] second)
        {
            double x = second[0] - first[0];
            double y = second[1] - first[1];
            double z = second[2] - first[2];
            return Math.Sqrt(x * x + y * y + z * z);
        }

        private static double UnitDot(double[] first, double[] vertex, double[] third)
        {
            double[] left = { first[0] - vertex[0], first[1] - vertex[1],
                first[2] - vertex[2] };
            double[] right = { third[0] - vertex[0], third[1] - vertex[1],
                third[2] - vertex[2] };
            return Dot(left, right) / (Length(left) * Length(right));
        }

        private static bool TryValidateDisplayContract(string mode, bool faceted, bool edges,
            string hiddenLines, out string message)
        {
            message = null;
            if (mode == "shaded" && (faceted || edges))
                message = "shaded requires faceted=false and edges=false.";
            else if (mode == "shaded_with_edges" && (faceted || !edges))
                message = "shaded_with_edges requires faceted=false and edges=true.";
            else if ((mode == "wireframe" || mode == "hidden_lines_visible" ||
                mode == "hidden_lines_removed") && !edges)
                message = mode + " requires edges=true.";
            else if (mode == "hidden_lines_removed" && hiddenLines != "removed")
                message = "hidden_lines_removed requires hidden_lines=removed.";
            else if ((mode == "shaded" || mode == "shaded_with_edges") && hiddenLines != "removed")
                message = mode + " requires hidden_lines=removed.";
            else if (mode == "hidden_lines_visible" && hiddenLines == "removed")
                message = "hidden_lines_visible cannot request hidden_lines=removed.";
            else if (mode == "wireframe" && hiddenLines == "removed")
                message = "wireframe cannot request hidden_lines=removed.";
            return message == null;
        }

        private static bool TryValidateProjectedGeometry(ViewPlanBasicViewSpec child,
            ViewPlanBasicViewSpec parent, string projectionMethod, out string message)
        {
            message = null;
            bool horizontal = child.ProjectionDirection == "left" ||
                child.ProjectionDirection == "right";
            bool vertical = child.ProjectionDirection == "up" ||
                child.ProjectionDirection == "down";
            if (!horizontal && !vertical)
            {
                message = "Projected view requires left, right, up, or down projection_direction.";
                return false;
            }
            if (horizontal && child.Alignment != "projected" && child.Alignment != "horizontal")
            {
                message = "Horizontal projection requires projected or horizontal alignment.";
                return false;
            }
            if (vertical && child.Alignment != "projected" && child.Alignment != "vertical")
            {
                message = "Vertical projection requires projected or vertical alignment.";
                return false;
            }
            if (Math.Abs(child.Scale - parent.Scale) > 1e-12)
            {
                message = "Projected view must inherit the exact parent scale.";
                return false;
            }
            double cross = horizontal ? child.Y - parent.Y : child.X - parent.X;
            if (Math.Abs(cross) > GeometryTolerance)
            {
                message = "Projected view must share its parent's aligned sheet coordinate.";
                return false;
            }
            int expectedSign = child.ProjectionDirection == "right" ||
                child.ProjectionDirection == "up" ? 1 : -1;
            if (projectionMethod == "first_angle") expectedSign *= -1;
            double delta = horizontal ? child.X - parent.X : child.Y - parent.Y;
            if (delta * expectedSign <= GeometryTolerance)
            {
                message = "Projected-view position conflicts with " + projectionMethod + ".";
                return false;
            }
            return true;
        }

        private static bool TryTopologicalOrder(List<ViewPlanBasicViewSpec> source,
            Dictionary<string, ViewPlanBasicViewSpec> byId,
            out List<ViewPlanBasicViewSpec> ordered)
        {
            ordered = new List<ViewPlanBasicViewSpec>();
            var indegree = source.ToDictionary(item => item.Id, item => 0, StringComparer.Ordinal);
            var children = source.ToDictionary(item => item.Id,
                item => new List<ViewPlanBasicViewSpec>(), StringComparer.Ordinal);
            foreach (ViewPlanBasicViewSpec item in source)
            {
                if (string.IsNullOrEmpty(item.ParentId)) continue;
                indegree[item.Id]++;
                children[item.ParentId].Add(item);
            }
            var ready = source.Where(item => indegree[item.Id] == 0)
                .OrderBy(item => item.OriginalIndex).ToList();
            while (ready.Count > 0)
            {
                ViewPlanBasicViewSpec current = ready[0];
                ready.RemoveAt(0);
                ordered.Add(current);
                foreach (ViewPlanBasicViewSpec child in children[current.Id]
                    .OrderBy(item => item.OriginalIndex))
                {
                    indegree[child.Id]--;
                    if (indegree[child.Id] == 0)
                    {
                        ready.Add(child);
                        ready = ready.OrderBy(item => item.OriginalIndex).ToList();
                    }
                }
            }
            return ordered.Count == source.Count;
        }

        private static bool HasItems(JToken token)
        {
            var array = token as JArray;
            return array != null && array.Count > 0;
        }

        private static bool TryFiniteTuple(JArray values, int count, out double first,
            out double second)
        {
            first = second = 0.0;
            return values != null && values.Count == count &&
                TryFinite(values[0], out first) && TryFinite(values[1], out second);
        }

        private static bool TryFiniteRect(JObject value, out double xMin, out double yMin,
            out double xMax, out double yMax)
        {
            xMin = yMin = xMax = yMax = 0.0;
            return value != null && TryFinite(value["x_min_m"], out xMin) &&
                TryFinite(value["y_min_m"], out yMin) &&
                TryFinite(value["x_max_m"], out xMax) &&
                TryFinite(value["y_max_m"], out yMax) &&
                xMax - xMin > GeometryTolerance && yMax - yMin > GeometryTolerance;
        }

        private static bool IsNull(JToken token)
        {
            return token == null || token.Type == JTokenType.Null;
        }

        private static bool TryFiniteVector3(JArray values, out double[] vector)
        {
            vector = null;
            if (values == null || values.Count != 3) return false;
            double x;
            double y;
            double z;
            if (!TryFinite(values[0], out x) || !TryFinite(values[1], out y) ||
                !TryFinite(values[2], out z)) return false;
            vector = new[] { x, y, z };
            return true;
        }

        private static double Dot(double[] first, double[] second)
        {
            return first[0] * second[0] + first[1] * second[1] + first[2] * second[2];
        }

        private static double[] Cross(double[] first, double[] second)
        {
            return new[]
            {
                first[1] * second[2] - first[2] * second[1],
                first[2] * second[0] - first[0] * second[2],
                first[0] * second[1] - first[1] * second[0]
            };
        }

        private static double Length(double[] vector)
        {
            return Math.Sqrt(Dot(vector, vector));
        }

        private static void Normalize(double[] vector, double length)
        {
            for (int index = 0; index < vector.Length; index++) vector[index] /= length;
        }

        private static bool TryFinitePositive(JToken token, out double value)
        {
            return TryFinite(token, out value) && value > 0.0;
        }

        private static bool TryFinite(JToken token, out double value)
        {
            value = 0.0;
            if (token == null || (token.Type != JTokenType.Float && token.Type != JTokenType.Integer))
                return false;
            value = token.Value<double>();
            return !double.IsNaN(value) && !double.IsInfinity(value);
        }

        private static string Pointer(int index, params string[] segments)
        {
            string value = "/views/" + index;
            foreach (string segment in segments) value += "/" + segment;
            return value;
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

    public sealed class ViewPlanBasicExecutionPlan
    {
        public string PlanId { get; internal set; }
        public string PlanCanonicalSha256 { get; internal set; }
        public string ModelPath { get; internal set; }
        public string ModelSha256 { get; internal set; }
        public string DrawingPath { get; internal set; }
        public string DrawingSha256 { get; internal set; }
        public string Configuration { get; internal set; }
        public string DisplayState { get; internal set; }
        public string ProjectionMethod { get; internal set; }
        public string SheetName { get; internal set; }
        public double SheetWidth { get; internal set; }
        public double SheetHeight { get; internal set; }
        public int SheetScaleNumerator { get; internal set; }
        public int SheetScaleDenominator { get; internal set; }
        public string TransientModelViewPolicy { get; internal set; }
        public IList<ViewPlanBasicViewSpec> Views { get; internal set; }
        public IList<ViewPlanBoundArtifact> InputArtifacts { get; internal set; }
    }

    public sealed class ViewPlanBoundArtifact
    {
        public string Role { get; internal set; }
        public string Path { get; internal set; }
        public string Sha256 { get; internal set; }
    }

    public sealed class ViewPlanBasicViewSpec
    {
        public int OriginalIndex { get; internal set; }
        public string Id { get; internal set; }
        public string Type { get; internal set; }
        public string ParentId { get; internal set; }
        public string ProjectionDirection { get; internal set; }
        public string Alignment { get; internal set; }
        public string OrientationKind { get; internal set; }
        public string OrientationName { get; internal set; }
        public double[] ViewDirectionModel { get; internal set; }
        public double[] UpDirectionModel { get; internal set; }
        public double RollAngleRad { get; internal set; }
        public double X { get; internal set; }
        public double Y { get; internal set; }
        public double Scale { get; internal set; }
        public double PlacementXMin { get; internal set; }
        public double PlacementYMin { get; internal set; }
        public double PlacementXMax { get; internal set; }
        public double PlacementYMax { get; internal set; }
        public string DisplayMode { get; internal set; }
        public bool Faceted { get; internal set; }
        public bool Edges { get; internal set; }
        public string HiddenLines { get; internal set; }
        public string TangentEdges { get; internal set; }
        public string SectionCuttingPlaneMode { get; internal set; }
        public IList<string> SectionFeatureIds { get; internal set; }
        public IList<double[]> SectionPointsModel { get; internal set; }
        public IList<double[]> SectionFeatureAxisOriginsModel { get; internal set; }
        public IList<double[]> SectionFeatureAxisDirectionsModel { get; internal set; }
        public string SectionCuttingLineAxis { get; internal set; }
        public double? SectionLineExtensionRatio { get; internal set; }
        public bool SectionReverseDirection { get; internal set; }
        public double SectionDepth { get; internal set; }
        public string SectionLabel { get; internal set; }
        public double ProfileOffsetX { get; internal set; }
        public double ProfileOffsetY { get; internal set; }
        public double ProfileRadiusSheet { get; internal set; }
        public double BrokenOutDepth { get; internal set; }
        public string DetailStyleName { get; internal set; }
        public int DetailStyle { get; internal set; }
        public string DetailShowTypeName { get; internal set; }
        public int DetailShowType { get; internal set; }
        public bool DetailFullOutline { get; internal set; }
        public bool DetailJaggedOutline { get; internal set; }
        public bool DetailNoOutline { get; internal set; }
        public int DetailShapeIntensity { get; internal set; }
        public string DetailLabel { get; internal set; }
        public string DetailLabelPositionMode { get; internal set; }
        public double? DetailLabelX { get; internal set; }
        public double? DetailLabelY { get; internal set; }
        public double[] AuxiliaryReferenceEdgeStartModel { get; internal set; }
        public double[] AuxiliaryReferenceEdgeEndModel { get; internal set; }
        public double AuxiliaryMatchToleranceSheet { get; internal set; }
        public bool AuxiliaryNotAligned { get; internal set; }
        public bool AuxiliaryShowArrow { get; internal set; }
        public bool AuxiliaryFlip { get; internal set; }
        public string AuxiliaryLabel { get; internal set; }
        public string AuxiliaryLabelPositionMode { get; internal set; }
        public double? AuxiliaryLabelX { get; internal set; }
        public double? AuxiliaryLabelY { get; internal set; }
        public IList<ViewPlanCenterMarkSpec> CenterMarks { get; internal set; }
        public IList<ViewPlanSymmetryCenterlineSpec> SymmetryCenterlines { get; internal set; }
    }

    public sealed class ViewPlanCenterMarkSpec
    {
        public string Id { get; internal set; }
        public int OriginalIndex { get; internal set; }
        public IList<string> FeatureIds { get; internal set; }
        public int ExpectedCount { get; internal set; }
        public string StyleName { get; internal set; }
        public int Style { get; internal set; }
        public bool UseDocumentDefaults { get; internal set; }
        public bool ShowLines { get; internal set; }
        public bool Propagate { get; internal set; }
        public bool Slot { get; internal set; }
        public int Color { get; internal set; }
        public IList<ViewPlanCircularEdgeSpec> CircularEdges { get; internal set; }
    }

    public sealed class ViewPlanCircularEdgeSpec
    {
        public string FeatureId { get; internal set; }
        public string EdgeId { get; internal set; }
        public double[] CenterModel { get; internal set; }
        public double[] AxisModel { get; internal set; }
        public double RadiusModel { get; internal set; }
    }

    public sealed class ViewPlanSymmetryCenterlineSpec
    {
        public string Id { get; internal set; }
        public int OriginalIndex { get; internal set; }
        public string Axis { get; internal set; }
        public double MinimumEdgeSpanRatio { get; internal set; }
        public int Color { get; internal set; }
    }

    public sealed class ViewPlanExecutionContractError
    {
        public string Code { get; internal set; }
        public string JsonPointer { get; internal set; }
        public string Message { get; internal set; }
    }
}
