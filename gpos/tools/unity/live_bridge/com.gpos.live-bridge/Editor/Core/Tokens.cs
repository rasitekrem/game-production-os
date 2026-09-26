// GPOS live bridge — optimistic-concurrency tokens and the component catalog digest (Unity-free core).
// A token is SHA-256 over a canonical, length-prefixed sequence of fields, truncated to 32 hex digits. Each token
// kind starts with its own domain string, so tokens of different kinds never collide by construction. A float is
// its IEEE-754 binary32 bit pattern (negative zero written as zero); nothing is formatted with a culture.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Security.Cryptography;
using System.Text;

namespace Gpos.LiveBridge
{
    internal sealed class TokenBuilder
    {
        public const string ObjectDomain = "gpos.obj/1";
        public const string TransformDomain = "gpos.xf/1";
        public const string ChainDomain = "gpos.chain/1";
        public const string ComponentDomain = "gpos.comp/1";
        public const string SubtreeDomain = "gpos.subtree/1";
        public const string RootsDomain = "gpos.roots/1";
        public const string CatalogDomain = "gpos.catalog/1";

        readonly StringBuilder text = new StringBuilder();

        public TokenBuilder(string domain) { Field(domain); }

        public TokenBuilder Field(string value)
        {
            value = value ?? "";
            text.Append(value.Length.ToString(CultureInfo.InvariantCulture)).Append(':').Append(value).Append(';');
            return this;
        }

        public TokenBuilder Bool(bool value) { return Field(value ? "1" : "0"); }

        public TokenBuilder Int(long value) { return Field(value.ToString(CultureInfo.InvariantCulture)); }

        public TokenBuilder UInt(ulong value) { return Field(value.ToString(CultureInfo.InvariantCulture)); }

        public TokenBuilder Float(float value) { return Field(FloatBits(value)); }

        public TokenBuilder Floats(params float[] values)
        {
            foreach (var v in values) Float(v);
            return this;
        }

        public string Text { get { return text.ToString(); } }

        public string Finish() { return Sha256Hex(text.ToString()).Substring(0, 32); }

        public string FinishFull() { return Sha256Hex(text.ToString()); }

        public static string FloatBits(float value)
        {
            if (value == 0f) value = 0f;   // -0 and +0 are one value
            int bits = BitConverter.ToInt32(BitConverter.GetBytes(value), 0);
            return bits.ToString("x8", CultureInfo.InvariantCulture);
        }

        public static string Sha256Hex(string s)
        {
            using (var sha = SHA256.Create())
                return string.Concat(sha.ComputeHash(Encoding.UTF8.GetBytes(s)).Select(b => b.ToString("x2", CultureInfo.InvariantCulture)));
        }
    }

    // One catalogued component type: everything that decides whether and how it can be added.
    internal sealed class CatalogEntry
    {
        public string TypeId, Assembly, FullName, Name, Namespace, Kind;   // Kind: "NATIVE" or "SCRIPT"
        public bool DisallowMultiple, RunsInEditMode;
        public List<string> Requires = new List<string>();

        public Dictionary<string, object> ToData()
        {
            return new Dictionary<string, object> {
                { "type_id", TypeId }, { "name", Name }, { "namespace", Namespace ?? "" }, { "assembly", Assembly },
                { "full_name", FullName }, { "kind", Kind }, { "disallow_multiple", DisallowMultiple },
                { "requires", Requires.Cast<object>().ToList() }, { "runs_in_edit_mode", RunsInEditMode } };
        }
    }

    internal static class CatalogDigest
    {
        // SHA-256 over the entries in ordinal type-id order; each entry contributes every field of CatalogEntry
        // (requirements sorted and de-duplicated), so a change of a type's attributes changes the digest even when
        // its id does not.
        public static string Of(IEnumerable<CatalogEntry> entries)
        {
            var ordered = entries.OrderBy(e => e.TypeId, StringComparer.Ordinal).ToList();
            var b = new TokenBuilder(TokenBuilder.CatalogDomain).Int(ordered.Count);
            foreach (var e in ordered)
            {
                var requires = e.Requires.Distinct().OrderBy(r => r, StringComparer.Ordinal).ToList();
                b.Field(e.TypeId).Field(e.Assembly).Field(e.FullName).Field(e.Name).Field(e.Namespace ?? "").Field(e.Kind)
                 .Bool(e.DisallowMultiple).Bool(e.RunsInEditMode).Int(requires.Count);
                foreach (var r in requires) b.Field(r);
            }
            return b.FinishFull();
        }
    }
}
