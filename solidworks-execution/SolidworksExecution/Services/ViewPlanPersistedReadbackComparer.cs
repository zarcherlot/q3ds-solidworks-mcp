using System;
using System.Linq;
using Newtonsoft.Json.Linq;

namespace SolidworksExecution.Services
{
    /// <summary>
    /// Compares normalized SolidWorks readback captured before and after save/reopen.
    /// Structure and non-numeric values remain exact; finite numeric leaves use the bounded
    /// persistence tolerance required for SolidWorks' on-disk coordinate round-trip.
    /// </summary>
    internal static class ViewPlanPersistedReadbackComparer
    {
        internal const double GeometryTolerance = 1e-3;
        internal const double ScalarTolerance = 1e-9;

        internal static bool Close(double expected, double actual,
            double tolerance = GeometryTolerance)
        {
            return !double.IsNaN(expected) && !double.IsInfinity(expected) &&
                !double.IsNaN(actual) && !double.IsInfinity(actual) &&
                Math.Abs(expected - actual) <= tolerance;
        }

        internal static bool Equivalent(JToken expected, JToken actual)
        {
            return Equivalent(expected, actual, false);
        }

        private static bool Equivalent(JToken expected, JToken actual,
            bool geometryContext)
        {
            if (ReferenceEquals(expected, actual)) return true;
            if (expected == null || actual == null) return false;
            if (IsNumeric(expected.Type) && IsNumeric(actual.Type))
                return Close(expected.Value<double>(), actual.Value<double>(),
                    geometryContext ? GeometryTolerance : ScalarTolerance);
            if (expected.Type != actual.Type) return false;

            if (expected is JObject expectedObject && actual is JObject actualObject)
            {
                if (expectedObject.Count != actualObject.Count) return false;
                foreach (JProperty property in expectedObject.Properties())
                {
                    JProperty actualProperty = actualObject.Property(property.Name,
                        StringComparison.Ordinal);
                    if (actualProperty == null ||
                        !Equivalent(property.Value, actualProperty.Value,
                            IsGeometryProperty(property.Name))) return false;
                }
                return true;
            }

            if (expected is JArray expectedArray && actual is JArray actualArray)
            {
                if (expectedArray.Count != actualArray.Count) return false;
                return expectedArray.Zip(actualArray,
                    (left, right) => Equivalent(left, right, geometryContext)).All(equal => equal);
            }

            return JToken.DeepEquals(expected, actual);
        }

        private static bool IsNumeric(JTokenType type)
        {
            return type == JTokenType.Integer || type == JTokenType.Float;
        }

        private static bool IsGeometryProperty(string name)
        {
            return name != null && (name.EndsWith("_m", StringComparison.Ordinal) ||
                name.IndexOf("_m_", StringComparison.Ordinal) >= 0);
        }
    }
}
