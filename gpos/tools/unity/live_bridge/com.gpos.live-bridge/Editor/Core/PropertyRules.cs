// GPOS live bridge — the SerializedProperty write allowlist and value validation (Unity-free core).
// Default deny: a property is writable only when its path has the plain grammar, no path segment is denied, and
// its (property type, type name) pair maps to one supported kind. A value is parsed and range-checked here before
// Unity ever sees it, because SerializedProperty itself silently clamps, wraps, turns out-of-range floats into
// infinity and accepts non-unit quaternions. What Unity actually stored is read back and compared afterwards.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;

namespace Gpos.LiveBridge
{
    internal sealed class PropertyValue
    {
        public string Kind;
        public bool Bool;
        public long Long;
        public ulong ULong;
        public double Double;
        public float Float;
        public string String;     // string, enum name or object id; null for a null object reference
        public float[] Floats;    // vectors, rect, color, quaternion (normalized), bounds (center then extents)
        public int[] Ints;        // integer vectors, rectint, boundsint (position then size)
    }

    internal static class PropertyRules
    {
        public const int MaxPathLength = 256;
        public const int MaxSegments = 6;
        public const int MaxString = 4096;
        public const double QuaternionTolerance = 1e-4;

        static readonly Regex PathGrammar = new Regex("^[A-Za-z_][A-Za-z0-9_]*(\\.[A-Za-z_][A-Za-z0-9_]*){0,5}\\z");
        static readonly Regex Decimal = new Regex("^-?(0|[1-9][0-9]{0,19})\\z");
        static readonly Regex PPtr = new Regex("^PPtr<\\$?([A-Za-z_][A-Za-z0-9_]*)>\\z");

        // Engine-owned serialized state that is never written, wherever it appears in a path.
        public static readonly HashSet<string> Denied = new HashSet<string>(StringComparer.Ordinal) {
            "m_Script", "m_GameObject", "m_Name", "m_ObjectHideFlags", "m_EditorHideFlags", "m_PrefabInstance",
            "m_PrefabAsset", "m_CorrespondingSourceObject", "m_EditorClassIdentifier", "m_PrefabParentObject",
            "m_PrefabInternal", "m_Father", "m_Children", "m_Component" };

        // kind -> (SerializedPropertyType name, SerializedProperty.type or null when any type name is that kind)
        public static readonly Dictionary<string, string[]> Kinds = new Dictionary<string, string[]>(StringComparer.Ordinal) {
            { "bool", new[] { "Boolean", "bool" } },
            { "int8", new[] { "Integer", "sbyte" } }, { "int16", new[] { "Integer", "short" } },
            { "int32", new[] { "Integer", "int" } }, { "int64", new[] { "Integer", "long" } },
            { "uint8", new[] { "Integer", "byte" } }, { "uint16", new[] { "Integer", "ushort" } },
            { "uint32", new[] { "Integer", "uint" } }, { "uint64", new[] { "Integer", "ulong" } },
            { "float32", new[] { "Float", "float" } }, { "float64", new[] { "Float", "double" } },
            { "string", new[] { "String", "string" } }, { "enum", new[] { "Enum", null } },
            { "vector2", new[] { "Vector2", null } }, { "vector3", new[] { "Vector3", null } }, { "vector4", new[] { "Vector4", null } },
            { "vector2int", new[] { "Vector2Int", null } }, { "vector3int", new[] { "Vector3Int", null } },
            { "rect", new[] { "Rect", null } }, { "rectint", new[] { "RectInt", null } },
            { "bounds", new[] { "Bounds", null } }, { "boundsint", new[] { "BoundsInt", null } },
            { "color", new[] { "Color", null } }, { "quaternion", new[] { "Quaternion", null } },
            { "layermask", new[] { "LayerMask", null } }, { "object", new[] { "ObjectReference", null } },
        };

        // The one supported kind of a property, or null (refused).
        public static string KindOf(string propertyType, string typeName)
        {
            foreach (var kv in Kinds)
                if (kv.Value[0] == propertyType && (kv.Value[1] == null || kv.Value[1] == typeName))
                    return kv.Key == "object" && PPtrType(typeName) == null ? null : kv.Key;
            return null;
        }

        // Why a path can never be written ("PATH_UNSUPPORTED" or "DENIED"), or null.
        public static string PathProblem(string path)
        {
            if (path == null || path.Length == 0 || path.Length > MaxPathLength || !PathGrammar.IsMatch(path)) return "PATH_UNSUPPORTED";
            foreach (var segment in path.Split('.'))
                if (Denied.Contains(segment)) return "DENIED";
            return null;
        }

        // Every proper prefix of a path ("a.b.c" -> "a", "a.b").
        public static List<string> Ancestors(string path)
        {
            var out_ = new List<string>();
            int i = -1;
            while ((i = path.IndexOf('.', i + 1)) >= 0) out_.Add(path.Substring(0, i));
            return out_;
        }

        // The simple type name T of an object-reference field "PPtr<T>" / "PPtr<$T>", or null.
        public static string PPtrType(string typeName)
        {
            if (typeName == null) return null;
            var m = PPtr.Match(typeName);
            return m.Success ? m.Groups[1].Value : null;
        }

        static Refusal Invalid(string message) { return new Refusal("VALUE_INVALID", message); }

        public static float ToFloat32(object v)
        {
            if (!(v is double)) throw Invalid("a number is expected");
            double d = (double)v;
            if (double.IsNaN(d) || double.IsInfinity(d) || Math.Abs(d) > float.MaxValue) throw Invalid("the number is not a finite 32-bit float");
            return (float)d;
        }

        static long Whole(object v, long min, long max)
        {
            if (!(v is double)) throw Invalid("a whole number is expected");
            double d = (double)v;
            if (double.IsNaN(d) || double.IsInfinity(d) || d != Math.Floor(d)) throw Invalid("a whole number is expected");
            if (d < min || d > max) throw Invalid("the number is outside " + min + ".." + max);
            return (long)d;
        }

        static List<object> List(object v, int count)
        {
            var l = v as List<object>;
            if (l == null || l.Count != count) throw Invalid("an array of " + count + " values is expected");
            return l;
        }

        static float[] Floats(object v, int count)
        {
            var l = List(v, count);
            var out_ = new float[count];
            for (int i = 0; i < count; i++) out_[i] = ToFloat32(l[i]);
            return out_;
        }

        static int[] Ints(object v, int count)
        {
            var l = List(v, count);
            var out_ = new int[count];
            for (int i = 0; i < count; i++) out_[i] = (int)Whole(l[i], int.MinValue, int.MaxValue);
            return out_;
        }

        public static float[] UnitQuaternion(object v)
        {
            var q = Floats(v, 4);
            double norm = Math.Sqrt((double)q[0] * q[0] + (double)q[1] * q[1] + (double)q[2] * q[2] + (double)q[3] * q[3]);
            if (Math.Abs(norm - 1.0) > QuaternionTolerance) throw Invalid("a rotation is a unit quaternion [x, y, z, w]");
            return new[] { (float)(q[0] / norm), (float)(q[1] / norm), (float)(q[2] / norm), (float)(q[3] / norm) };
        }

        public static void CheckString(string s)
        {
            if (s.Length > MaxString) throw Invalid("a string has at most " + MaxString + " characters");
            for (int i = 0; i < s.Length; i++)
            {
                if (char.IsHighSurrogate(s[i]) && i + 1 < s.Length && char.IsLowSurrogate(s[i + 1])) { i++; continue; }
                if (char.IsSurrogate(s[i])) throw Invalid("a string holds an unpaired surrogate");
            }
        }

        // Parses and validates a value for a kind. Throws VALUE_INVALID; never touches Unity.
        public static PropertyValue Parse(string kind, object v)
        {
            if (kind == null || !Kinds.ContainsKey(kind)) throw new Refusal("PROPERTY_UNSUPPORTED", "the kind is not a supported property kind");
            var p = new PropertyValue { Kind = kind };
            switch (kind)
            {
                case "bool":
                    if (!(v is bool)) throw Invalid("true or false is expected");
                    p.Bool = (bool)v; break;
                case "int8": p.Long = Whole(v, sbyte.MinValue, sbyte.MaxValue); break;
                case "int16": p.Long = Whole(v, short.MinValue, short.MaxValue); break;
                case "int32": p.Long = Whole(v, int.MinValue, int.MaxValue); break;
                case "uint8": p.Long = Whole(v, byte.MinValue, byte.MaxValue); break;
                case "uint16": p.Long = Whole(v, ushort.MinValue, ushort.MaxValue); break;
                case "uint32":
                case "layermask": p.Long = Whole(v, 0, uint.MaxValue); break;
                case "int64":
                {
                    var s = v as string;
                    if (s == null || !Decimal.IsMatch(s) || !long.TryParse(s, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out p.Long))
                        throw Invalid("a 64-bit integer is a decimal string within the int64 range");
                    break;
                }
                case "uint64":
                {
                    var s = v as string;
                    if (s == null || !Decimal.IsMatch(s) || s.StartsWith("-", StringComparison.Ordinal) ||
                        !ulong.TryParse(s, NumberStyles.None, CultureInfo.InvariantCulture, out p.ULong))
                        throw Invalid("an unsigned 64-bit integer is a decimal string within the uint64 range");
                    break;
                }
                case "float32": p.Float = ToFloat32(v); break;
                case "float64":
                {
                    if (!(v is double) || double.IsNaN((double)v) || double.IsInfinity((double)v)) throw Invalid("a finite number is expected");
                    p.Double = (double)v; break;
                }
                case "string":
                {
                    var s = v as string;
                    if (s == null) throw Invalid("a string is expected");
                    CheckString(s);
                    p.String = s; break;
                }
                case "enum":
                {
                    var s = v as string;
                    if (s == null || s.Length == 0 || s.Length > 256) throw Invalid("an enum value is one of its names");
                    p.String = s; break;
                }
                case "vector2": p.Floats = Floats(v, 2); break;
                case "vector3": p.Floats = Floats(v, 3); break;
                case "vector4":
                case "rect":
                case "color": p.Floats = Floats(v, 4); break;
                case "quaternion": p.Floats = UnitQuaternion(v); break;
                case "vector2int": p.Ints = Ints(v, 2); break;
                case "vector3int": p.Ints = Ints(v, 3); break;
                case "rectint": p.Ints = Ints(v, 4); break;
                case "bounds":
                {
                    var l = List(v, 2);
                    var c = Floats(l[0], 3);
                    var e = Floats(l[1], 3);
                    if (e[0] < 0 || e[1] < 0 || e[2] < 0) throw Invalid("bounds extents are not negative");
                    p.Floats = new[] { c[0], c[1], c[2], e[0], e[1], e[2] };
                    break;
                }
                case "boundsint":
                {
                    var l = List(v, 2);
                    var a = Ints(l[0], 3);
                    var b = Ints(l[1], 3);
                    p.Ints = new[] { a[0], a[1], a[2], b[0], b[1], b[2] };
                    break;
                }
                case "object":
                    if (v != null && !(v is string)) throw Invalid("an object reference is null or a Scene object id");
                    p.String = (string)v;
                    if (p.String != null) ObjectIds.SceneGuid(p.String);
                    break;
            }
            return p;
        }
    }
}
