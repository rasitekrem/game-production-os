// GPOS live bridge — strict, bounded JSON (Unity-free core).
// Objects become Dictionary<string, object>, arrays List<object>, numbers double; strings, bools and null as
// themselves. Duplicate keys, trailing data, non-JSON tokens and anything beyond the bounds are refused.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace Gpos.LiveBridge
{
    internal sealed class JsonProblem : Exception
    {
        public JsonProblem(string message) : base(message) { }
    }

    internal static class Json
    {
        public const int MaxDepth = 8;
        public const int MaxString = 8192;
        public const int MaxItems = 256;

        public static object Parse(string text)
        {
            if (text == null) throw new JsonProblem("no text");
            int i = 0;
            object value = Value(text, ref i, 0);
            Space(text, ref i);
            if (i != text.Length) throw new JsonProblem("trailing data");
            return value;
        }

        static void Space(string s, ref int i)
        {
            while (i < s.Length && (s[i] == ' ' || s[i] == '\t' || s[i] == '\n' || s[i] == '\r')) i++;
        }

        static object Value(string s, ref int i, int depth)
        {
            if (depth > MaxDepth) throw new JsonProblem("nesting too deep");
            Space(s, ref i);
            if (i >= s.Length) throw new JsonProblem("unexpected end");
            char c = s[i];
            if (c == '{') return Object(s, ref i, depth);
            if (c == '[') return Array(s, ref i, depth);
            if (c == '"') return String(s, ref i);
            if (Literal(s, ref i, "true")) return true;
            if (Literal(s, ref i, "false")) return false;
            if (Literal(s, ref i, "null")) return null;
            return Number(s, ref i);
        }

        static Dictionary<string, object> Object(string s, ref int i, int depth)
        {
            var d = new Dictionary<string, object>(StringComparer.Ordinal);
            i++;
            Space(s, ref i);
            if (i < s.Length && s[i] == '}') { i++; return d; }
            while (true)
            {
                Space(s, ref i);
                if (i >= s.Length || s[i] != '"') throw new JsonProblem("expected a key");
                string key = String(s, ref i);
                if (d.ContainsKey(key)) throw new JsonProblem("duplicate key");
                if (d.Count >= MaxItems) throw new JsonProblem("too many keys");
                Space(s, ref i);
                if (i >= s.Length || s[i] != ':') throw new JsonProblem("expected ':'");
                i++;
                d[key] = Value(s, ref i, depth + 1);
                Space(s, ref i);
                if (i < s.Length && s[i] == ',') { i++; continue; }
                if (i < s.Length && s[i] == '}') { i++; return d; }
                throw new JsonProblem("expected ',' or '}'");
            }
        }

        static List<object> Array(string s, ref int i, int depth)
        {
            var l = new List<object>();
            i++;
            Space(s, ref i);
            if (i < s.Length && s[i] == ']') { i++; return l; }
            while (true)
            {
                if (l.Count >= MaxItems) throw new JsonProblem("too many items");
                l.Add(Value(s, ref i, depth + 1));
                Space(s, ref i);
                if (i < s.Length && s[i] == ',') { i++; continue; }
                if (i < s.Length && s[i] == ']') { i++; return l; }
                throw new JsonProblem("expected ',' or ']'");
            }
        }

        static bool Literal(string s, ref int i, string literal)
        {
            if (string.CompareOrdinal(s, i, literal, 0, literal.Length) != 0) return false;
            i += literal.Length;
            return true;
        }

        static double Number(string s, ref int i)
        {
            int start = i;
            if (i < s.Length && s[i] == '-') i++;
            while (i < s.Length && (char.IsDigit(s[i]) || s[i] == '.' || s[i] == 'e' || s[i] == 'E' || s[i] == '+' || s[i] == '-')) i++;
            double n;
            if (i == start || i - start > 32 ||
                !double.TryParse(s.Substring(start, i - start), NumberStyles.Float, CultureInfo.InvariantCulture, out n) ||
                double.IsNaN(n) || double.IsInfinity(n))
                throw new JsonProblem("not a JSON value");
            return n;
        }

        static string String(string s, ref int i)
        {
            var sb = new StringBuilder();
            i++;
            while (true)
            {
                if (i >= s.Length) throw new JsonProblem("unterminated string");
                if (sb.Length > MaxString) throw new JsonProblem("string too long");
                char c = s[i++];
                if (c == '"') return sb.ToString();
                if (c < 0x20) throw new JsonProblem("control character in a string");
                if (c != '\\') { sb.Append(c); continue; }
                if (i >= s.Length) throw new JsonProblem("bad escape");
                char e = s[i++];
                switch (e)
                {
                    case '"': sb.Append('"'); break;
                    case '\\': sb.Append('\\'); break;
                    case '/': sb.Append('/'); break;
                    case 'b': sb.Append('\b'); break;
                    case 'f': sb.Append('\f'); break;
                    case 'n': sb.Append('\n'); break;
                    case 'r': sb.Append('\r'); break;
                    case 't': sb.Append('\t'); break;
                    case 'u':
                        int code;
                        if (i + 4 > s.Length || !int.TryParse(s.Substring(i, 4), NumberStyles.AllowHexSpecifier, CultureInfo.InvariantCulture, out code))
                            throw new JsonProblem("bad unicode escape");
                        sb.Append((char)code);
                        i += 4;
                        break;
                    default: throw new JsonProblem("bad escape");
                }
            }
        }

        public static string Write(object value)
        {
            var sb = new StringBuilder();
            Write(sb, value);
            return sb.ToString();
        }

        static void Write(StringBuilder sb, object v)
        {
            if (v == null) { sb.Append("null"); return; }
            var str = v as string;
            if (str != null) { Quote(sb, str); return; }
            if (v is bool) { sb.Append((bool)v ? "true" : "false"); return; }
            if (v is int || v is long) { sb.Append(Convert.ToInt64(v).ToString(CultureInfo.InvariantCulture)); return; }
            if (v is double || v is float) { sb.Append(Convert.ToDouble(v).ToString("R", CultureInfo.InvariantCulture)); return; }
            var d = v as IDictionary<string, object>;
            if (d != null)
            {
                sb.Append('{');
                bool first = true;
                foreach (var kv in d)
                {
                    if (!first) sb.Append(',');
                    first = false;
                    Quote(sb, kv.Key);
                    sb.Append(':');
                    Write(sb, kv.Value);
                }
                sb.Append('}');
                return;
            }
            var l = v as System.Collections.IEnumerable;
            if (l != null)
            {
                sb.Append('[');
                bool first = true;
                foreach (var x in l)
                {
                    if (!first) sb.Append(',');
                    first = false;
                    Write(sb, x);
                }
                sb.Append(']');
                return;
            }
            Quote(sb, v.ToString());
        }

        static void Quote(StringBuilder sb, string s)
        {
            sb.Append('"');
            foreach (char c in s)
            {
                if (c == '"') sb.Append("\\\"");
                else if (c == '\\') sb.Append("\\\\");
                else if (c < 0x20) sb.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                else sb.Append(c);
            }
            sb.Append('"');
        }
    }
}
