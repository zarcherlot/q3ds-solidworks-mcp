using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace SolidworksExecution.Contracts
{
    /// <summary>
    /// Offline, COM-free validator for the repository-owned solidworks-view-plan 1.4 contract.
    /// The authoritative Schema is linked from drawing_planner/contracts at build time; this class
    /// implements only the Draft 2020-12 keywords used by that locked contract and fails closed on
    /// any unsupported Schema keyword that can affect instance validation.
    /// </summary>
    public sealed class ViewPlanContractValidator
    {
        public const string ProtocolId = "solidworks-view-plan";
        public const string SchemaVersion = "1.4";
        public const string ContractSha256 =
            "ebe92b04bd1b4a4f0fd7ff6a6314e36f531e06421b0ae8f803fbb86ab209ceac";

        private const int MaximumValidationDepth = 256;
        private static readonly Regex Rfc3339DateTime = new Regex(
            @"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$",
            RegexOptions.CultureInvariant);
        private static readonly HashSet<string> SupportedKeywords = new HashSet<string>(
            new[]
            {
                "$schema", "$id", "$defs", "$ref", "title", "description",
                "type", "const", "enum", "allOf", "oneOf", "if", "then",
                "properties", "required", "additionalProperties", "items", "prefixItems",
                "minItems", "maxItems", "uniqueItems", "minLength", "maxLength", "pattern",
                "format", "minimum", "maximum", "exclusiveMinimum"
            },
            StringComparer.Ordinal);

        private readonly JObject _schema;
        private readonly string _contractLabel;
        private readonly string _errorCode;

        public ViewPlanContractValidator(string schemaPath)
            : this(schemaPath, ContractSha256, "ViewPlan", "VIEW_PLAN_SCHEMA_INVALID")
        {
        }

        internal ViewPlanContractValidator(string schemaPath, string expectedSha256,
            string contractLabel, string errorCode)
        {
            if (string.IsNullOrWhiteSpace(schemaPath) || !Path.IsPathRooted(schemaPath))
                throw new ArgumentException(contractLabel + " schema path must be absolute.", "schemaPath");
            string fullPath = Path.GetFullPath(schemaPath);
            if (!File.Exists(fullPath))
                throw new FileNotFoundException(contractLabel + " schema file was not found.", fullPath);

            _contractLabel = string.IsNullOrWhiteSpace(contractLabel) ? "JSON contract" : contractLabel;
            _errorCode = string.IsNullOrWhiteSpace(errorCode) ? "SCHEMA_INVALID" : errorCode;

            byte[] bytes = File.ReadAllBytes(fullPath);
            string actualHash = ComputeSha256(bytes);
            if (!string.Equals(actualHash, expectedSha256, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException(
                    _contractLabel + " schema SHA-256 mismatch. expected=" + expectedSha256 +
                    " actual=" + actualHash);
            string text = new UTF8Encoding(false, true).GetString(bytes);
            _schema = JObject.Parse(text, new JsonLoadSettings
            {
                DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error,
                CommentHandling = CommentHandling.Ignore,
                LineInfoHandling = LineInfoHandling.Ignore
            });
            ValidateSchemaVocabulary(_schema, "#", 0);
        }

        public bool TryParse(JToken candidate, out ViewPlanDocument document,
            out ViewPlanContractError error)
        {
            document = null;
            if (!TryValidate(candidate, out error))
                return false;

            var root = (JObject)candidate;
            var views = (JArray)root["views"];
            var canonical = (JObject)Canonicalize(root);
            document = new ViewPlanDocument
            {
                ProtocolId = root.Value<string>("protocol_id"),
                SchemaVersion = root.Value<string>("schema_version"),
                PlanId = root.Value<string>("plan_id"),
                ModelPath = root.Value<string>("model_path"),
                DrawingPath = root.Value<string>("drawing_path"),
                MainViewId = root.Value<string>("main_view_id"),
                ViewTypes = views.Select(item => item.Value<string>("type")).ToArray(),
                CanonicalPlan = canonical,
                CanonicalSha256 = ComputeSha256(
                    Encoding.UTF8.GetBytes(PythonCompatibleCanonicalJson(canonical)))
            };
            return true;
        }

        internal bool TryValidate(JToken candidate, out ViewPlanContractError error)
        {
            error = null;
            if (candidate == null || candidate.Type != JTokenType.Object)
            {
                error = NewError("", _contractLabel + " candidate must be a JSON object.");
                return false;
            }
            return ValidateNode(candidate, _schema, "", 0, out error);
        }

        private bool ValidateNode(JToken instance, JObject schema, string pointer, int depth,
            out ViewPlanContractError error)
        {
            error = null;
            if (depth > MaximumValidationDepth)
            {
                error = NewError(pointer, _contractLabel + " exceeds the maximum validation depth.");
                return false;
            }

            string reference = schema.Value<string>("$ref");
            if (reference != null)
            {
                JObject resolved;
                if (!TryResolveReference(reference, out resolved))
                {
                    error = NewError(pointer, "Contract contains an unresolved $ref: " + reference);
                    return false;
                }
                if (!ValidateNode(instance, resolved, pointer, depth + 1, out error))
                    return false;
            }

            JToken typeRule = schema["type"];
            if (typeRule != null && !MatchesType(instance, typeRule))
            {
                error = NewError(pointer, "Value does not match the required JSON type.");
                return false;
            }

            JToken constant = schema["const"];
            if (constant != null && !JToken.DeepEquals(instance, constant))
            {
                error = NewError(pointer, "Value does not match the required constant.");
                return false;
            }

            var enumValues = schema["enum"] as JArray;
            if (enumValues != null && !enumValues.Any(value => JToken.DeepEquals(instance, value)))
            {
                error = NewError(pointer, "Value is not one of the allowed enum values.");
                return false;
            }

            var allOf = schema["allOf"] as JArray;
            if (allOf != null)
            {
                foreach (JObject branch in allOf.OfType<JObject>())
                    if (!ValidateNode(instance, branch, pointer, depth + 1, out error))
                        return false;
            }

            var oneOf = schema["oneOf"] as JArray;
            if (oneOf != null)
            {
                int matches = 0;
                foreach (JObject branch in oneOf.OfType<JObject>())
                {
                    ViewPlanContractError ignored;
                    if (ValidateNode(instance, branch, pointer, depth + 1, out ignored))
                        matches++;
                }
                if (matches != 1)
                {
                    error = NewError(pointer,
                        "Value must match exactly one contract branch; matched " + matches + ".");
                    return false;
                }
            }

            var condition = schema["if"] as JObject;
            var consequence = schema["then"] as JObject;
            if (condition != null && consequence != null)
            {
                ViewPlanContractError ignored;
                if (ValidateNode(instance, condition, pointer, depth + 1, out ignored) &&
                    !ValidateNode(instance, consequence, pointer, depth + 1, out error))
                    return false;
            }

            var obj = instance as JObject;
            if (obj != null && !ValidateObject(obj, schema, pointer, depth, out error))
                return false;
            var array = instance as JArray;
            if (array != null && !ValidateArray(array, schema, pointer, depth, out error))
                return false;
            if (instance.Type == JTokenType.String &&
                !ValidateString(instance.Value<string>(), schema, pointer, out error))
                return false;
            if ((instance.Type == JTokenType.Integer || instance.Type == JTokenType.Float) &&
                !ValidateNumber(instance, schema, pointer, out error))
                return false;

            return true;
        }

        private bool ValidateObject(JObject instance, JObject schema, string pointer, int depth,
            out ViewPlanContractError error)
        {
            error = null;
            var required = schema["required"] as JArray;
            if (required != null)
            {
                foreach (JToken item in required)
                {
                    string name = item.Value<string>();
                    if (name == null || instance.Property(name, StringComparison.Ordinal) == null)
                    {
                        error = NewError(AppendPointer(pointer, name ?? ""),
                            "Required property is missing.");
                        return false;
                    }
                }
            }

            var properties = schema["properties"] as JObject;
            if (properties != null)
            {
                foreach (JProperty property in properties.Properties())
                {
                    JToken value = instance[property.Name];
                    var childSchema = property.Value as JObject;
                    if (value != null && childSchema != null &&
                        !ValidateNode(value, childSchema, AppendPointer(pointer, property.Name),
                            depth + 1, out error))
                        return false;
                }
            }

            JToken additional = schema["additionalProperties"];
            if (additional != null && additional.Type == JTokenType.Boolean &&
                additional.Value<bool>() == false)
            {
                var allowed = new HashSet<string>(
                    properties != null
                        ? properties.Properties().Select(property => property.Name)
                        : Enumerable.Empty<string>(),
                    StringComparer.Ordinal);
                JProperty unknown = instance.Properties().FirstOrDefault(
                    property => !allowed.Contains(property.Name));
                if (unknown != null)
                {
                    error = NewError(AppendPointer(pointer, unknown.Name),
                        "Unknown property is forbidden by the " + _contractLabel + " contract.");
                    return false;
                }
            }
            return true;
        }

        private bool ValidateArray(JArray instance, JObject schema, string pointer, int depth,
            out ViewPlanContractError error)
        {
            error = null;
            int? minimum = schema.Value<int?>("minItems");
            int? maximum = schema.Value<int?>("maxItems");
            if (minimum.HasValue && instance.Count < minimum.Value)
            {
                error = NewError(pointer, "Array contains fewer than minItems elements.");
                return false;
            }
            if (maximum.HasValue && instance.Count > maximum.Value)
            {
                error = NewError(pointer, "Array contains more than maxItems elements.");
                return false;
            }
            if (schema.Value<bool?>("uniqueItems") == true)
            {
                for (int i = 0; i < instance.Count; i++)
                    for (int j = i + 1; j < instance.Count; j++)
                        if (JToken.DeepEquals(instance[i], instance[j]))
                        {
                            error = NewError(AppendPointer(pointer, j.ToString(CultureInfo.InvariantCulture)),
                                "Array elements must be unique.");
                            return false;
                        }
            }

            var prefix = schema["prefixItems"] as JArray;
            int prefixCount = prefix != null ? prefix.Count : 0;
            if (prefix != null)
            {
                for (int i = 0; i < Math.Min(prefix.Count, instance.Count); i++)
                {
                    var childSchema = prefix[i] as JObject;
                    if (childSchema != null &&
                        !ValidateNode(instance[i], childSchema,
                            AppendPointer(pointer, i.ToString(CultureInfo.InvariantCulture)),
                            depth + 1, out error))
                        return false;
                }
            }

            JToken itemRule = schema["items"];
            if (itemRule != null && itemRule.Type == JTokenType.Boolean &&
                itemRule.Value<bool>() == false && instance.Count > prefixCount)
            {
                error = NewError(AppendPointer(pointer,
                    prefixCount.ToString(CultureInfo.InvariantCulture)),
                    "Additional array elements are forbidden.");
                return false;
            }
            var itemSchema = itemRule as JObject;
            if (itemSchema != null)
            {
                for (int i = prefixCount; i < instance.Count; i++)
                    if (!ValidateNode(instance[i], itemSchema,
                        AppendPointer(pointer, i.ToString(CultureInfo.InvariantCulture)),
                        depth + 1, out error))
                        return false;
            }
            return true;
        }

        private bool ValidateString(string value, JObject schema, string pointer,
            out ViewPlanContractError error)
        {
            error = null;
            int? minimum = schema.Value<int?>("minLength");
            int? maximum = schema.Value<int?>("maxLength");
            if (minimum.HasValue && value.Length < minimum.Value)
            {
                error = NewError(pointer, "String is shorter than minLength.");
                return false;
            }
            if (maximum.HasValue && value.Length > maximum.Value)
            {
                error = NewError(pointer, "String is longer than maxLength.");
                return false;
            }
            string pattern = schema.Value<string>("pattern");
            if (pattern != null && !Regex.IsMatch(value, pattern, RegexOptions.CultureInvariant))
            {
                error = NewError(pointer, "String does not match the required pattern.");
                return false;
            }
            string format = schema.Value<string>("format");
            if (format == "date-time")
            {
                DateTimeOffset parsed;
                if (!Rfc3339DateTime.IsMatch(value) ||
                    !DateTimeOffset.TryParse(value, CultureInfo.InvariantCulture,
                        DateTimeStyles.RoundtripKind, out parsed))
                {
                    error = NewError(pointer, "String is not an RFC 3339 date-time.");
                    return false;
                }
            }
            return true;
        }

        private bool ValidateNumber(JToken instance, JObject schema, string pointer,
            out ViewPlanContractError error)
        {
            error = null;
            double value = instance.Value<double>();
            if (double.IsNaN(value) || double.IsInfinity(value))
            {
                error = NewError(pointer, "Number must be finite.");
                return false;
            }
            double? minimum = schema.Value<double?>("minimum");
            double? maximum = schema.Value<double?>("maximum");
            double? exclusiveMinimum = schema.Value<double?>("exclusiveMinimum");
            if (minimum.HasValue && value < minimum.Value)
            {
                error = NewError(pointer, "Number is below minimum.");
                return false;
            }
            if (maximum.HasValue && value > maximum.Value)
            {
                error = NewError(pointer, "Number is above maximum.");
                return false;
            }
            if (exclusiveMinimum.HasValue && value <= exclusiveMinimum.Value)
            {
                error = NewError(pointer, "Number must be greater than exclusiveMinimum.");
                return false;
            }
            return true;
        }

        private static bool MatchesType(JToken value, JToken rule)
        {
            var array = rule as JArray;
            if (array != null)
                return array.Any(item => item.Type == JTokenType.String &&
                    MatchesSingleType(value, item.Value<string>()));
            return rule.Type == JTokenType.String &&
                MatchesSingleType(value, rule.Value<string>());
        }

        private static bool MatchesSingleType(JToken value, string type)
        {
            switch (type)
            {
                case "object": return value.Type == JTokenType.Object;
                case "array": return value.Type == JTokenType.Array;
                case "string": return value.Type == JTokenType.String;
                case "integer": return value.Type == JTokenType.Integer;
                case "number": return value.Type == JTokenType.Integer || value.Type == JTokenType.Float;
                case "boolean": return value.Type == JTokenType.Boolean;
                case "null": return value.Type == JTokenType.Null;
                default: return false;
            }
        }

        private bool TryResolveReference(string reference, out JObject resolved)
        {
            resolved = null;
            if (reference == "#")
            {
                resolved = _schema;
                return true;
            }
            if (reference == null || !reference.StartsWith("#/", StringComparison.Ordinal))
                return false;
            JToken current = _schema;
            foreach (string encoded in reference.Substring(2).Split('/'))
            {
                string segment = encoded.Replace("~1", "/").Replace("~0", "~");
                var currentObject = current as JObject;
                if (currentObject == null || currentObject[segment] == null)
                    return false;
                current = currentObject[segment];
            }
            resolved = current as JObject;
            return resolved != null;
        }

        private void ValidateSchemaVocabulary(JToken token, string pointer, int depth)
        {
            if (depth > MaximumValidationDepth)
                throw new InvalidDataException(_contractLabel + " contract exceeds the maximum schema depth.");
            var obj = token as JObject;
            if (obj != null)
            {
                foreach (JProperty property in obj.Properties())
                {
                    // Objects under properties/$defs are name maps, not Schema keyword sets.
                    if (pointer.EndsWith("/properties", StringComparison.Ordinal) ||
                        pointer.EndsWith("/$defs", StringComparison.Ordinal))
                    {
                        ValidateSchemaVocabulary(property.Value,
                            pointer + "/" + EscapePointer(property.Name), depth + 1);
                        continue;
                    }
                    if (!SupportedKeywords.Contains(property.Name))
                        throw new InvalidDataException(
                            "Unsupported " + _contractLabel + " Schema keyword at " + pointer + ": " + property.Name);
                    ValidateSchemaVocabulary(property.Value,
                        pointer + "/" + EscapePointer(property.Name), depth + 1);
                }
                return;
            }
            var array = token as JArray;
            if (array != null)
                for (int i = 0; i < array.Count; i++)
                    ValidateSchemaVocabulary(array[i], pointer + "/" + i, depth + 1);
        }

        private static JToken Canonicalize(JToken token)
        {
            var obj = token as JObject;
            if (obj != null)
            {
                var sorted = new JObject();
                foreach (JProperty property in obj.Properties().OrderBy(
                    property => property.Name, StringComparer.Ordinal))
                    sorted[property.Name] = Canonicalize(property.Value);
                return sorted;
            }
            var array = token as JArray;
            if (array != null)
                return new JArray(array.Select(Canonicalize));
            return token.DeepClone();
        }

        private static string PythonCompatibleCanonicalJson(JToken token)
        {
            string json = token.ToString(Formatting.None);
            var result = new StringBuilder(json.Length);
            bool inString = false;
            bool escaped = false;
            for (int index = 0; index < json.Length; index++)
            {
                char current = json[index];
                if (inString)
                {
                    result.Append(current);
                    if (escaped) escaped = false;
                    else if (current == '\\') escaped = true;
                    else if (current == '"') inString = false;
                    continue;
                }
                if (current == '"')
                {
                    inString = true;
                    result.Append(current);
                    continue;
                }
                if (current == 'E' && index > 0 && index + 2 < json.Length &&
                    Char.IsDigit(json[index - 1]) &&
                    (json[index + 1] == '+' || json[index + 1] == '-') &&
                    Char.IsDigit(json[index + 2])) current = 'e';
                result.Append(current);
            }
            return result.ToString();
        }

        private static string AppendPointer(string pointer, string segment)
        {
            return pointer + "/" + EscapePointer(segment);
        }

        private static string EscapePointer(string value)
        {
            return (value ?? "").Replace("~", "~0").Replace("/", "~1");
        }

        private ViewPlanContractError NewError(string pointer, string message)
        {
            return new ViewPlanContractError
            {
                Code = _errorCode,
                JsonPointer = pointer ?? "",
                Message = message
            };
        }

        private static string ComputeSha256(byte[] payload)
        {
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(payload)).Replace("-", "").ToLowerInvariant();
        }
    }

    public sealed class ViewPlanDocument
    {
        public string ProtocolId { get; internal set; }
        public string SchemaVersion { get; internal set; }
        public string PlanId { get; internal set; }
        public string ModelPath { get; internal set; }
        public string DrawingPath { get; internal set; }
        public string MainViewId { get; internal set; }
        public string[] ViewTypes { get; internal set; }
        public JObject CanonicalPlan { get; internal set; }
        public string CanonicalSha256 { get; internal set; }
    }

    public sealed class ViewPlanContractError
    {
        public string Code { get; internal set; }
        public string JsonPointer { get; internal set; }
        public string Message { get; internal set; }
    }
}
