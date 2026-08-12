using System;
using System.Collections.Generic;
using System.Linq;

namespace SolidworksExecution.Contracts
{
    /// <summary>
    /// Deterministic mapping from repository-observed SolidWorks feature-tree/FeatureData facts
    /// to the experimental mechanical taxonomy.  This class deliberately has no COM dependency
    /// so the policy can be contract-tested without starting SolidWorks.
    /// </summary>
    internal static class SemanticFeatureTypeContract
    {
        private static readonly HashSet<int> CompoundWizardHoleTypes = new HashSet<int>
        {
            2, 3, 4, 7, 8, 9,
            10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21,
            23, 24, 26, 27, 28, 29, 30,
            43, 44, 45
        };

        private static readonly HashSet<int> ThroughWizardHoleTypes = new HashSet<int>
        {
            14, 15, 16, 17, 18, 19, 20, 21,
            25, 26, 27, 28, 29, 30,
            33, 34, 35, 36, 39, 40, 41, 42,
            47, 48, 49, 50, 51, 52, 53, 54, 55,
            61, 62, 63, 64, 65, 66, 67, 68,
            72, 73, 74, 75, 76, 77, 79, 90
        };

        internal static string Classify(string typeName, bool? extrudeIsBoss,
            int? wizardHoleType)
        {
            string type = typeName ?? string.Empty;
            if (wizardHoleType.HasValue || string.Equals(type, "HoleWzd",
                StringComparison.OrdinalIgnoreCase))
                return ClassifyWizardHole(wizardHoleType);

            if (string.Equals(type, "Hole", StringComparison.OrdinalIgnoreCase) ||
                string.Equals(type, "SimpleHole", StringComparison.OrdinalIgnoreCase))
                return "geometry.hole.blind_drilled";

            if (string.Equals(type, "Rib", StringComparison.OrdinalIgnoreCase))
                return "geometry.positive.rib";
            if (string.Equals(type, "Fillet", StringComparison.OrdinalIgnoreCase) ||
                string.Equals(type, "VarFillet", StringComparison.OrdinalIgnoreCase) ||
                string.Equals(type, "Chamfer", StringComparison.OrdinalIgnoreCase))
                return "geometry.transition";
            if (string.Equals(type, "Shell", StringComparison.OrdinalIgnoreCase))
                return "geometry.thin_wall_or_shell";
            if (string.Equals(type, "CosmeticThread", StringComparison.OrdinalIgnoreCase) ||
                string.Equals(type, "SweepThread", StringComparison.OrdinalIgnoreCase))
                return "structure.thread";
            if (type.StartsWith("SM", StringComparison.OrdinalIgnoreCase) ||
                string.Equals(type, "EdgeFlange", StringComparison.OrdinalIgnoreCase) ||
                string.Equals(type, "OneBend", StringComparison.OrdinalIgnoreCase))
                return "structure.sheet_metal_form";

            if (extrudeIsBoss == true)
                return "geometry.positive.boss_hub_lug_or_foot";
            if (extrudeIsBoss == false)
                return "geometry.pocket";
            return null;
        }

        internal static string ClassifyWizardHole(int? wizardHoleType)
        {
            if (!wizardHoleType.HasValue) return null;
            int value = wizardHoleType.Value;
            if (value >= 57 && value <= 90) return "geometry.slot.obround";
            if (CompoundWizardHoleTypes.Contains(value)) return "geometry.hole.compound";
            return ThroughWizardHoleTypes.Contains(value)
                ? "geometry.hole.through"
                : "geometry.hole.blind_drilled";
        }

        internal static bool IsThroughWizardHole(int? wizardHoleType)
        {
            return wizardHoleType.HasValue && ThroughWizardHoleTypes.Contains(
                wizardHoleType.Value);
        }

        internal static string ClassifyExtrudedCutProfile(int fullCircleCount,
            bool hasOtherProfileGeometry, bool through)
        {
            if (fullCircleCount <= 0 || hasOtherProfileGeometry) return "geometry.pocket";
            return through ? "geometry.hole.through" : "geometry.hole.blind_drilled";
        }

        internal static bool IsPattern(string typeName)
        {
            return string.Equals(typeName, "CirPattern", StringComparison.OrdinalIgnoreCase) ||
                string.Equals(typeName, "LPattern", StringComparison.OrdinalIgnoreCase);
        }

        internal static bool IsMirror(string typeName)
        {
            return string.Equals(typeName, "MirrorPattern", StringComparison.OrdinalIgnoreCase) ||
                string.Equals(typeName, "MirrorSolid", StringComparison.OrdinalIgnoreCase);
        }

        internal static bool TryReadCircularTransformAxis(double[] first, double[] second,
            out double[] origin, out double[] direction)
        {
            origin = null;
            direction = null;
            if (first == null || second == null || first.Length < 12 || second.Length < 12)
                return false;

            // SolidWorks MathTransform stores the 3x3 rotation followed by translation.  For
            // a non-identity circular-pattern transform, the rotation axis is the eigenvector
            // with eigenvalue one.  The skew-symmetric terms give it without relying on a
            // selected reference entity; reject the degenerate 0 and pi cases fail-closed.
            double x = second[7] - second[5];
            double y = second[2] - second[6];
            double z = second[3] - second[1];
            double magnitude = Math.Sqrt(x * x + y * y + z * z);
            if (magnitude < 1e-10) return false;
            direction = new[] { x / magnitude, y / magnitude, z / magnitude };

            double cosine = Math.Max(-1.0, Math.Min(1.0,
                (second[0] + second[4] + second[8] - 1.0) / 2.0));
            double sine = magnitude / 2.0;
            if (Math.Abs(sine) < 1e-10) return false;
            double tx = second[9];
            double ty = second[10];
            double tz = second[11];
            double crossX = direction[1] * tz - direction[2] * ty;
            double crossY = direction[2] * tx - direction[0] * tz;
            double crossZ = direction[0] * ty - direction[1] * tx;
            double cotangentHalfAngle = (1.0 + cosine) / sine;
            origin = new[]
            {
                0.5 * (tx + cotangentHalfAngle * crossX),
                0.5 * (ty + cotangentHalfAngle * crossY),
                0.5 * (tz + cotangentHalfAngle * crossZ)
            };
            return origin.All(value => !double.IsNaN(value) && !double.IsInfinity(value)) &&
                direction.All(value => !double.IsNaN(value) && !double.IsInfinity(value));
        }
    }
}
