using System;

namespace SolidworksExecution.Contracts
{
    /// <summary>
    /// COM-free native section-view rules shared by contract tests and the SolidWorks executor.
    /// Drawing sketch entities are relative to the active drawing view, while
    /// ModelToViewTransform points include the view's sheet insertion position.
    /// </summary>
    public static class ViewPlanSectionNativeContract
    {
        private const int NotAligned = 1;
        private const int OffsetSection = 2;
        private const int ChangeDirection = 4;
        private const int Partial = 16;
        private const int DisplaySurfaceCut = 32;

        public static double[] ToActiveViewLocal(double[] sheetPoint, double[] viewPosition)
        {
            if (sheetPoint == null || sheetPoint.Length < 2 || viewPosition == null ||
                viewPosition.Length < 2) return null;
            return new[] { sheetPoint[0] - viewPosition[0],
                sheetPoint[1] - viewPosition[1], 0.0 };
        }

        public static int CreateOptions(string type, string alignment, bool reverseDirection)
        {
            int options = DisplaySurfaceCut;
            if (alignment == "not_aligned") options |= NotAligned;
            if (type == "aligned_section") options |= OffsetSection;
            if (reverseDirection) options |= ChangeDirection;
            if (type == "half_section") options |= Partial;
            return options;
        }
    }
}
