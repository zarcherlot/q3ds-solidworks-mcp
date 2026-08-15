using System;
using System.Collections.Generic;
using System.Linq;

namespace SolidworksExecution.Services
{
    internal sealed class SectionSymbolGeometry
    {
        public IList<double[]> Points { get; set; }
        public bool Exact { get; set; }
    }

    internal static class LayoutDisplayGeometry
    {
        public static bool AddArrowHead(ICollection<double[]> points,
            double[] values)
        {
            if (points == null || values == null || values.Length < 8 ||
                !values.Take(8).All(IsFinite)) return false;
            double x = values[0];
            double y = values[1];
            double dx = values[3];
            double dy = values[4];
            double length = Math.Abs(values[6]);
            double height = Math.Abs(values[7]);
            double magnitude = Math.Sqrt(dx * dx + dy * dy);
            if (magnitude <= 1e-15 || length <= 0.0 || height <= 0.0)
                return false;
            dx /= magnitude;
            dy /= magnitude;
            double baseX = x - dx * length;
            double baseY = y - dy * length;
            double perpendicularX = -dy * height / 2.0;
            double perpendicularY = dx * height / 2.0;
            points.Add(new[] { x, y });
            points.Add(new[] { baseX + perpendicularX,
                baseY + perpendicularY });
            points.Add(new[] { baseX - perpendicularX,
                baseY - perpendicularY });
            return true;
        }

        public static bool AddTextRectangle(ICollection<double[]> points,
            double anchorX, double anchorY, double width, double height,
            double angle, int reference)
        {
            if (points == null || !new[] { anchorX, anchorY, width, height,
                angle }.All(IsFinite) || width <= 0.0 || height <= 0.0 ||
                reference < 0 || reference > 5) return false;
            double left;
            double bottom;
            switch (reference)
            {
                case 0: // upper left
                    left = 0.0; bottom = -height; break;
                case 1: // lower left
                    left = 0.0; bottom = 0.0; break;
                case 2: // center
                    left = -width / 2.0; bottom = -height / 2.0; break;
                case 3: // upper right
                    left = -width; bottom = -height; break;
                case 4: // lower right
                    left = -width; bottom = 0.0; break;
                default: // upper center
                    left = -width / 2.0; bottom = -height; break;
            }
            double cosine = Math.Cos(angle);
            double sine = Math.Sin(angle);
            foreach (double[] corner in new[]
            {
                new[] { left, bottom },
                new[] { left, bottom + height },
                new[] { left + width, bottom },
                new[] { left + width, bottom + height }
            })
                points.Add(new[]
                {
                    anchorX + corner[0] * cosine - corner[1] * sine,
                    anchorY + corner[0] * sine + corner[1] * cosine
                });
            return true;
        }

        public static IList<SectionSymbolGeometry> ParseSectionLineInfo2(
            double[] values, double viewX, double viewY)
        {
            var result = new List<SectionSymbolGeometry>();
            if (values == null || values.Length < 2 ||
                !IsFinite(viewX) || !IsFinite(viewY)) return result;
            int cursor = 0;
            int sectionCount;
            if (!TryReadCount(values, ref cursor, out sectionCount)) return result;
            cursor++; // layer id
            for (int sectionIndex = 0; sectionIndex < sectionCount; sectionIndex++)
            {
                int segmentCount;
                if (!TryReadCount(values, ref cursor, out segmentCount))
                    return new List<SectionSymbolGeometry>();
                var points = new List<double[]>();
                bool exact = true;
                for (int segment = 0; segment < segmentCount; segment++)
                {
                    if (cursor + 7 > values.Length) return new List<SectionSymbolGeometry>();
                    cursor++; // line type
                    double[] start = values.Skip(cursor).Take(3).ToArray(); cursor += 3;
                    double[] end = values.Skip(cursor).Take(3).ToArray(); cursor += 3;
                    if (!start.All(IsFinite) || !end.All(IsFinite)) exact = false;
                    else
                    {
                        // Section segment coordinates are relative to the parent view.
                        points.Add(new[] { start[0] + viewX, start[1] + viewY });
                        points.Add(new[] { end[0] + viewX, end[1] + viewY });
                    }
                }
                var textCenters = new List<double[]>();
                for (int arrowIndex = 0; arrowIndex < 2; arrowIndex++)
                {
                    if (cursor + 9 > values.Length) return new List<SectionSymbolGeometry>();
                    double[] start = values.Skip(cursor).Take(3).ToArray(); cursor += 3;
                    double[] end = values.Skip(cursor).Take(3).ToArray(); cursor += 3;
                    double width = values[cursor++];
                    double height = values[cursor++];
                    cursor++; // arrow style
                    if (!start.All(IsFinite) || !end.All(IsFinite))
                    {
                        exact = false;
                        textCenters.Add(new[] { Double.NaN, Double.NaN });
                        continue;
                    }
                    points.Add(new[] { start[0], start[1] });
                    points.Add(new[] { end[0], end[1] });
                    textCenters.Add(new[] { end[0], end[1] });
                    double[] arrow = { end[0], end[1], end[2],
                        end[0] - start[0], end[1] - start[1], end[2] - start[2],
                        width, height };
                    if (!AddArrowHead(points, arrow)) exact = false;
                }
                if (cursor + 7 > values.Length) return new List<SectionSymbolGeometry>();
                var origins = new List<double[]>();
                for (int textIndex = 0; textIndex < 2; textIndex++)
                {
                    origins.Add(values.Skip(cursor).Take(3).ToArray());
                    cursor += 3;
                }
                double textHeight = values[cursor++];
                for (int textIndex = 0; textIndex < 2; textIndex++)
                {
                    double[] origin = origins[textIndex];
                    double[] center = textCenters[textIndex];
                    double halfWidth = Math.Abs(center[0] - origin[0]);
                    if (!origin.All(IsFinite) || !center.All(IsFinite) ||
                        !IsFinite(textHeight) || textHeight <= 0.0 ||
                        halfWidth <= 0.0)
                    {
                        exact = false;
                        continue;
                    }
                    // GetTextInfo/GetSectionLineInfo2 returns each label's native
                    // upper-left origin. Section labels are centered on the paired
                    // arrow endpoint, so the origin-to-center offset is the native
                    // half width already used by SolidWorks.
                    double left = Math.Min(origin[0], 2.0 * center[0] - origin[0]);
                    double right = Math.Max(origin[0], 2.0 * center[0] - origin[0]);
                    points.Add(new[] { left, origin[1] - textHeight });
                    points.Add(new[] { right, origin[1] });
                }
                result.Add(new SectionSymbolGeometry { Points = points, Exact = exact });
            }
            if (cursor != values.Length) return new List<SectionSymbolGeometry>();
            return result;
        }

        private static bool TryReadCount(double[] values, ref int cursor,
            out int count)
        {
            count = 0;
            if (cursor >= values.Length || !IsFinite(values[cursor])) return false;
            double raw = values[cursor++];
            count = Convert.ToInt32(raw);
            return count >= 0 && Math.Abs(raw - count) <= 1e-9;
        }

        private static bool IsFinite(double value)
        {
            return !Double.IsNaN(value) && !Double.IsInfinity(value);
        }
    }
}
