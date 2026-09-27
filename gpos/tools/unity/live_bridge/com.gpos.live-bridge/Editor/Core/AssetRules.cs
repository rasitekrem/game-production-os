// GPOS live bridge — asset references and asset authoring rules (Unity-free core, bridge 1.2.0).
// An asset is named only by its GlobalObjectId string: identifier type 1 (an imported asset or one of its typed
// sub-assets), 3 (a source asset such as a .mat or .asset file) or 4 (a built-in resource of the two built-in GUIDs),
// always with prefab id 0. There is no InstanceID, EntityId, caller path, package-cache path or file name as identity.
// Assets are written only below Assets/, at a validated path the caller names exactly, with the one extension of the
// asset kind; this core decides every grammar, bound and value rule before Unity sees anything. The persistent
// asset-creation transaction record is parsed and validated here too: it is never trusted for a path.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.RegularExpressions;

namespace Gpos.LiveBridge
{
    internal static class AssetIds
    {
        public const string DefaultResourcesGuid = "0000000000000000e000000000000000";
        public const string BuiltinExtraGuid = "0000000000000000f000000000000000";

        static readonly Regex Asset = new Regex("^GlobalObjectId_V1-([134])-([0-9a-f]{32})-([0-9]{1,20})-0\\z");
        static readonly Regex Any = new Regex("^GlobalObjectId_V1-[0-9]+-[0-9a-f]{32}-[0-9]{1,20}-[0-9]{1,20}\\z");

        // The identifier type (1, 3 or 4) of an asset id; refuses (OBJECT_REFUSED) everything else.
        public static int Check(string id)
        {
            if (id == null) throw new Refusal("OBJECT_REFUSED", "an asset is named by its GlobalObjectId string");
            var m = Asset.Match(id);
            if (!m.Success)
            {
                if (Any.IsMatch(id))
                    throw new Refusal("OBJECT_REFUSED", "an asset id is a GlobalObjectId of identifier type 1, 3 or 4 with prefab id 0");
                throw new Refusal("OBJECT_REFUSED", "an asset id is a GlobalObjectId_V1-<type>-<guid>-<file id>-0 string");
            }
            ulong ignored;
            if (!ulong.TryParse(m.Groups[3].Value, NumberStyles.None, CultureInfo.InvariantCulture, out ignored))
                throw new Refusal("OBJECT_REFUSED", "the asset id's file id is out of range");
            int type = m.Groups[1].Value[0] - '0';
            string guid = m.Groups[2].Value;
            if (guid == ObjectIds.ZeroGuid) throw new Refusal("OBJECT_REFUSED", "the asset id has no asset GUID");
            if (type == 4 && guid != DefaultResourcesGuid && guid != BuiltinExtraGuid)
                throw new Refusal("OBJECT_REFUSED", "a built-in asset id names one of the two built-in resource GUIDs");
            if (type != 4 && (guid == DefaultResourcesGuid || guid == BuiltinExtraGuid))
                throw new Refusal("OBJECT_REFUSED", "the built-in resource GUIDs are named only by built-in (type 4) ids");
            return type;
        }

        public static string Guid(string id) { Check(id); return id.Split('-')[2]; }

        public static bool IsAsset(string id) { return id != null && Asset.IsMatch(id); }

        // An object reference value: a Scene object of a saved Scene, or an asset. Refuses anything else.
        public static void CheckReference(string id)
        {
            if (id != null && Asset.IsMatch(id)) { Check(id); return; }
            ObjectIds.SceneGuid(id);
        }
    }

    internal static class AssetPaths
    {
        public const int MaxPathLength = 512;
        public const int MaxSegments = 16;
        public const string ScratchPrefix = "GposAssetTxn-";

        static readonly Regex Segment = new Regex("^[A-Za-z0-9 _().,+\\-]{1,64}\\z");
        static readonly Regex Stem = new Regex("^[A-Za-z0-9_()\\-]([A-Za-z0-9 _()\\-]{0,62}[A-Za-z0-9_()\\-])?\\z");
        static readonly string[] Special = { "editor", "editor default resources", "streamingassets" };

        static Refusal Invalid(string why) { return new Refusal("ASSET_PATH_INVALID", why); }

        // Validates a path an asset is written to: "Assets/<folders>/<stem><ext>" with the exact extension of the
        // asset kind. Returns the parent folder. The folder must already exist; that is checked by the caller.
        public static string CheckWritePath(string path, string ext)
        {
            if (path == null || path.Length == 0 || path.Length > MaxPathLength) throw Invalid("an asset path has 1 to " + MaxPathLength + " characters");
            if (ext != ".mat" && ext != ".asset") throw Invalid("assets of this kind are never written");
            if (!path.StartsWith("Assets/", StringComparison.Ordinal)) throw Invalid("assets are written only below Assets/ (never Packages/, Library/ or an absolute path)");
            if (!path.EndsWith(ext, StringComparison.Ordinal)) throw Invalid("this kind of asset is written as a " + ext + " file");
            var parts = path.Split('/');
            if (parts.Length < 3 || parts.Length > MaxSegments + 2) throw Invalid("an asset is written into a folder below Assets/, at most " + MaxSegments + " folders deep");
            for (int i = 1; i < parts.Length - 1; i++)
            {
                string s = parts[i];
                if (!Segment.IsMatch(s) || s == "." || s == ".." || s.StartsWith(".", StringComparison.Ordinal) || s.EndsWith(".", StringComparison.Ordinal) ||
                    s.StartsWith(" ", StringComparison.Ordinal) || s.EndsWith(" ", StringComparison.Ordinal))
                    throw Invalid("folder name '" + s + "' is not a plain folder name");
                if (Special.Contains(s.ToLowerInvariant())) throw Invalid("assets are never written into a special folder (" + s + ")");
                if (s.StartsWith(ScratchPrefix, StringComparison.OrdinalIgnoreCase)) throw Invalid("GPOS transaction scratch folders are never a destination");
            }
            string file = parts[parts.Length - 1];
            string stem = file.Substring(0, file.Length - ext.Length);
            if (!Stem.IsMatch(stem)) throw Invalid("the file name is 1 to 64 letters, digits, spaces, _ ( ) or -, not starting or ending with a space");
            return string.Join("/", parts, 0, parts.Length - 1);
        }

        public static string StemOf(string path, string ext)
        {
            string file = path.Substring(path.LastIndexOf('/') + 1);
            return file.Substring(0, file.Length - ext.Length);
        }

        // The GPOS-owned scratch folder of a transaction and the temporary asset inside it: derived from the
        // transaction id and the validated final path only, never read from a record or chosen by a caller.
        public static string ScratchFolder(string txnId)
        {
            if (txnId == null || !Protocol.Hex32.IsMatch(txnId)) throw new Refusal("CREATE_INCOMPLETE", "a transaction id is 32 hex");
            return "Assets/" + ScratchPrefix + txnId;
        }

        public static string TempPath(string txnId, string finalPath, string ext)
        {
            return ScratchFolder(txnId) + "/" + StemOf(finalPath, ext) + ext;
        }
    }

    internal static class AssetKinds
    {
        public const string Material = "MATERIAL", Texture = "TEXTURE", Sprite = "SPRITE", Audio = "AUDIO", Mesh = "MESH",
                            Prefab = "PREFAB", PrefabComponent = "PREFAB_COMPONENT", Model = "MODEL", ScriptableObject = "SCRIPTABLE_OBJECT";
        public const string Assets = "ASSETS", Package = "PACKAGE", Builtin = "BUILTIN";

        public static readonly string[] All = { Material, Texture, Sprite, Audio, Mesh, Prefab, PrefabComponent, Model, ScriptableObject };
        public static readonly string[] Sources = { Assets, Package, Builtin };

        // kind -> (the fixed AssetDatabase type filter a lookup uses, what it names, authorable file extension or "")
        public static readonly Dictionary<string, string[]> Table = new Dictionary<string, string[]>(StringComparer.Ordinal) {
            { Material, new[] { "t:Material", "a Material (main asset or a model's embedded material)", ".mat" } },
            { Texture, new[] { "t:Texture", "a Texture2D, Cubemap, Texture3D, Texture2DArray or CubemapArray", "" } },
            { Sprite, new[] { "t:Sprite", "a Sprite (usually a sub-asset of an imported texture)", "" } },
            { Audio, new[] { "t:AudioClip", "an AudioClip or another AudioResource", "" } },
            { Mesh, new[] { "t:Mesh", "a Mesh (usually a sub-asset of an imported model)", "" } },
            { Prefab, new[] { "t:Prefab", "the root GameObject of a prefab asset", "" } },
            { PrefabComponent, new[] { "", "a Component on the root GameObject of a prefab or model asset", "" } },
            { Model, new[] { "t:Model", "the root GameObject of an imported model", "" } },
            { ScriptableObject, new[] { "t:ScriptableObject", "a ScriptableObject of a catalogued type", ".asset" } },
        };

        public static bool Findable(string kind) { return Table.ContainsKey(kind) && Table[kind][0].Length > 0; }

        public static string Extension(string kind) { string[] e; return Table.TryGetValue(kind, out e) ? e[2] : ""; }

        public static string Digest()
        {
            var b = new TokenBuilder(TokenBuilder.KindsDomain).Int(All.Length);
            foreach (var k in All) b.Field(k).Field(Table[k][0]).Field(Table[k][2]);
            return b.FinishFull();
        }

        // Texture dimension names (UnityEngine.Rendering.TextureDimension) a texture kind reference may have.
        public static readonly HashSet<string> TextureDimensions = new HashSet<string>(StringComparer.Ordinal) { "Tex2D", "Tex3D", "Cube", "Tex2DArray", "CubeArray" };
    }

    internal static class AssetBounds
    {
        public const long MaxFileBytes = 4L * 1024 * 1024;
        public const long MaxMetaBytes = 256L * 1024;
        public const int MaxFindFiles = 5000;
        public const int FindPage = 200;
        public const int MaxObjectsPerFile = 256;
        public const int MaxQuery = 64;
        public const int MaxShaders = 1024;
        public const int MaxShaderProperties = 128;
        public const int MaxRootComponents = 64;
        public const int MaxRecords = 16;
        public const int MaxRecordBytes = 4096;

        public static void CheckQuery(string query)
        {
            if (query != null && (query.Length > MaxQuery || query.Any(c => c < 0x20 || c == 0x7f)))
                throw new Refusal("BAD_ARGUMENTS", "query has at most " + MaxQuery + " printable characters");
        }
    }

    // The composite token of a mutable asset: identity, canonical path, runtime type, the whole in-memory serialized
    // state (an opaque hash), the dirty flag and the SHA-256 of the source file and of its .meta file.
    internal static class AssetToken
    {
        public static string Of(string id, string path, string type, string memory, bool dirty, string file, string meta)
        {
            return new TokenBuilder(TokenBuilder.AssetDomain).Field(id).Field(path).Field(type).Field(memory).Bool(dirty)
                .Field(file).Field(meta).Finish();
        }
    }

    // ------------------------------------------------------------ Material shader properties

    internal sealed class MaterialValue
    {
        public string Kind;
        public float Float;
        public int Int;
        public float[] Floats;
        public string TextureId;   // null clears the texture
    }

    internal static class MaterialRules
    {
        public static readonly string[] Kinds = { "color", "vector", "float", "range", "int", "texture" };

        // The kind of a shader property type (UnityEngine.Rendering.ShaderPropertyType name), or null.
        public static string KindOf(string shaderType)
        {
            switch (shaderType)
            {
                case "Color": return "color";
                case "Vector": return "vector";
                case "Float": return "float";
                case "Range": return "range";
                case "Int": return "int";
                case "Texture": return "texture";
            }
            return null;
        }

        // Why a declared shader property is never written through GPOS, or null.
        public static string FlagProblem(bool hideInInspector, bool perRendererData, bool nonModifiableTexture)
        {
            if (hideInInspector) return "HIDDEN";
            if (perRendererData) return "PER_RENDERER_DATA";
            if (nonModifiableTexture) return "NOT_EDITABLE";
            return null;
        }

        static Refusal Invalid(string message) { return new Refusal("VALUE_INVALID", message); }

        // Parses and validates a value for a shader property of `kind` (range limits apply to "range"). Never touches Unity.
        public static MaterialValue Parse(string kind, object v, float rangeMin, float rangeMax)
        {
            if (kind == null || !Kinds.Contains(kind)) throw new Refusal("PROPERTY_UNSUPPORTED", "the kind is one of " + string.Join(", ", Kinds));
            var m = new MaterialValue { Kind = kind };
            switch (kind)
            {
                case "color":
                case "vector":
                {
                    var l = v as List<object>;
                    if (l == null || l.Count != 4) throw Invalid("a " + kind + " is an array of 4 numbers");
                    m.Floats = l.Select(PropertyRules.ToFloat32).ToArray();
                    break;
                }
                case "float": m.Float = PropertyRules.ToFloat32(v); break;
                case "range":
                    m.Float = PropertyRules.ToFloat32(v);
                    if (m.Float < rangeMin || m.Float > rangeMax)
                        throw Invalid("the value is outside the shader's declared range " + rangeMin.ToString("R", CultureInfo.InvariantCulture) + ".." + rangeMax.ToString("R", CultureInfo.InvariantCulture));
                    break;
                case "int":
                {
                    if (!(v is double)) throw Invalid("a whole number is expected");
                    double d = (double)v;
                    if (d != Math.Floor(d) || d < int.MinValue || d > int.MaxValue) throw Invalid("a 32-bit whole number is expected");
                    m.Int = (int)d;
                    break;
                }
                case "texture":
                    if (v != null && !(v is string)) throw Invalid("a texture is null or a Texture asset id");
                    m.TextureId = (string)v;
                    if (m.TextureId != null) AssetIds.Check(m.TextureId);
                    break;
            }
            return m;
        }

        // Whether a texture of dimension `actual` fits a shader texture property declared `declared` (TextureDimension names).
        public static bool DimensionMatches(string declared, string actual)
        {
            if (!AssetKinds.TextureDimensions.Contains(actual)) return false;
            return declared == "Any" || declared == actual;
        }
    }

    // ------------------------------------------------------------ Renderer material slots

    internal static class RendererSlots
    {
        public const int MaxSlot = 7;
        public const string Replace = "REPLACE", CreateFirst = "CREATE_FIRST";

        // What writing `slot` of a renderer with `size` material slots does. Never appends, inserts, shrinks or resizes.
        public static string Operation(int slot, int size)
        {
            if (slot < 0 || slot > MaxSlot) throw new Refusal("BAD_ARGUMENTS", "slot is 0 to " + MaxSlot);
            if (slot < size) return Replace;
            if (size == 0 && slot == 0) return CreateFirst;
            throw new Refusal("PROPERTY_UNSUPPORTED", "the renderer has " + size + " material slot(s); only an existing slot is replaced (or slot 0 of a renderer with none) — slots are never appended, inserted or removed");
        }
    }

    // ------------------------------------------------------------ catalogs

    internal sealed class ScriptableEntry
    {
        public string TypeId, Assembly, FullName, Name, Namespace, MenuName, FileName;
        public bool ScriptMapped, Creatable;

        public Dictionary<string, object> ToData()
        {
            return new Dictionary<string, object> {
                { "type_id", TypeId }, { "name", Name }, { "namespace", Namespace ?? "" }, { "assembly", Assembly },
                { "full_name", FullName }, { "script_mapped", ScriptMapped }, { "creatable", Creatable },
                { "menu_name", MenuName ?? "" }, { "file_name", FileName ?? "" } };
        }
    }

    internal sealed class ShaderProperty
    {
        public string Name, Type, Dimension;
        public bool HideInInspector, PerRendererData, NonModifiableTexture;
        public float RangeMin, RangeMax;

        public string Kind { get { return MaterialRules.KindOf(Type); } }

        public string Refusal { get { return Kind == null ? "TYPE_UNSUPPORTED" : MaterialRules.FlagProblem(HideInInspector, PerRendererData, NonModifiableTexture); } }

        public Dictionary<string, object> ToData()
        {
            var d = new Dictionary<string, object> { { "name", Name }, { "type", Type }, { "kind", Kind }, { "writable", Refusal == null }, { "refusal", Refusal } };
            if (Type == "Range") { d["range_min"] = (double)RangeMin; d["range_max"] = (double)RangeMax; }
            if (Type == "Texture") d["dimension"] = Dimension;
            return d;
        }
    }

    internal sealed class ShaderEntry
    {
        public string Id, Name;
        public List<ShaderProperty> Properties = new List<ShaderProperty>();

        public Dictionary<string, object> ToData(bool withProperties)
        {
            var d = new Dictionary<string, object> { { "id", Id }, { "name", Name }, { "property_count", Properties.Count } };
            if (withProperties) d["properties"] = Properties.Select(p => (object)p.ToData()).ToList();
            return d;
        }
    }

    internal static class AssetCatalogDigest
    {
        // Every field of every entry, in ordinal type-id order: a change of whether a type is creatable, its menu or
        // its script mapping changes the digest even when its id does not.
        public static string ScriptableObjects(IEnumerable<ScriptableEntry> entries)
        {
            var ordered = entries.OrderBy(e => e.TypeId, StringComparer.Ordinal).ToList();
            var b = new TokenBuilder(TokenBuilder.ScriptableCatalogDomain).Int(ordered.Count);
            foreach (var e in ordered)
                b.Field(e.TypeId).Field(e.Assembly).Field(e.FullName).Field(e.Name).Field(e.Namespace ?? "")
                 .Bool(e.ScriptMapped).Bool(e.Creatable).Field(e.MenuName ?? "").Field(e.FileName ?? "");
            return b.FinishFull();
        }

        // Every shader in ordinal id order with every declared property in declaration order: name, type, the three
        // write-relevant flags, the range limits and the texture dimension.
        public static string Shaders(IEnumerable<ShaderEntry> entries)
        {
            var ordered = entries.OrderBy(e => e.Id, StringComparer.Ordinal).ToList();
            var b = new TokenBuilder(TokenBuilder.ShaderCatalogDomain).Int(ordered.Count);
            foreach (var e in ordered)
            {
                b.Field(e.Id).Field(e.Name).Int(e.Properties.Count);
                foreach (var p in e.Properties)
                    b.Field(p.Name).Field(p.Type).Bool(p.HideInInspector).Bool(p.PerRendererData).Bool(p.NonModifiableTexture)
                     .Float(p.RangeMin).Float(p.RangeMax).Field(p.Dimension ?? "");
            }
            return b.FinishFull();
        }
    }

    // ------------------------------------------------------------ the persistent asset-creation transaction

    // One asset creation, recorded under .game/gpos-runtime/unity/asset-create-txn/<project key>/<txn id>.json before
    // anything is written and updated at each proven step. Identifiers and hashes only: the scratch folder and the
    // temporary asset are derived from the transaction id and the validated final path; nothing in a record is ever
    // used as a path without that validation. A record that is not exactly this shape is never acted on.
    internal sealed class CreateTxn
    {
        public const string Schema = "gpos.unity.live-bridge.asset-create-txn/1";
        public const string Prepared = "PREPARED", ScratchReady = "SCRATCH_READY", TempProven = "TEMP_PROVEN", FinalProven = "FINAL_PROVEN";
        public static readonly string[] Phases = { Prepared, ScratchReady, TempProven, FinalProven };
        static readonly string[] Keys = { "schema", "txn_id", "project_key", "session_id", "request_id", "owner", "kind", "final_path",
                                          "phase", "scratch_meta_sha256", "guid", "global_id", "type", "temp_sha256", "temp_meta_sha256",
                                          "final_sha256", "final_meta_sha256", "started_utc" };
        static readonly Regex Hex64 = new Regex("^[0-9a-f]{64}\\z");
        static readonly Regex Utc = new Regex("^\\d{4}-\\d\\d-\\d\\dT\\d\\d:\\d\\d:\\d\\d(\\.\\d{1,7})?Z\\z");

        public string TxnId, ProjectKey, SessionId, RequestId, Owner, Kind, FinalPath, Phase, ScratchMeta, Guid, GlobalId, Type,
                      TempFile, TempMeta, FinalFile, FinalMeta, StartedUtc;

        public string Ext { get { return AssetKinds.Extension(Kind); } }
        public string ScratchFolder { get { return AssetPaths.ScratchFolder(TxnId); } }
        public string TempPath { get { return AssetPaths.TempPath(TxnId, FinalPath, Ext); } }
        public int PhaseIndex { get { return Array.IndexOf(Phases, Phase); } }

        static Refusal Bad(string why) { return new Refusal("CREATE_INCOMPLETE", "the asset-creation record is not trusted: " + why); }

        public string Write()
        {
            return Json.Write(new Dictionary<string, object> {
                { "schema", Schema }, { "txn_id", TxnId }, { "project_key", ProjectKey }, { "session_id", SessionId },
                { "request_id", RequestId }, { "owner", Owner }, { "kind", Kind }, { "final_path", FinalPath }, { "phase", Phase },
                { "scratch_meta_sha256", ScratchMeta }, { "guid", Guid }, { "global_id", GlobalId }, { "type", Type },
                { "temp_sha256", TempFile }, { "temp_meta_sha256", TempMeta }, { "final_sha256", FinalFile },
                { "final_meta_sha256", FinalMeta }, { "started_utc", StartedUtc } });
        }

        // The record in `text`, stored as <txnId>.json of project `projectKey`; CREATE_INCOMPLETE when it is not exact.
        public static CreateTxn Parse(string text, string fileTxnId, string projectKey)
        {
            if (text == null || text.Length > AssetBounds.MaxRecordBytes) throw Bad("it is larger than " + AssetBounds.MaxRecordBytes + " bytes");
            Dictionary<string, object> d;
            try { d = Json.Parse(text) as Dictionary<string, object>; }
            catch (JsonProblem e) { throw Bad("it is not strict JSON (" + e.Message + ")"); }
            if (d == null || d.Count != Keys.Length || Keys.Any(k => !d.ContainsKey(k))) throw Bad("it does not have exactly the record keys");
            foreach (var k in Keys)
                if (d[k] != null && !(d[k] is string)) throw Bad(k + " is not a string");
            Func<string, string> s = k => (string)d[k];
            if (s("schema") != Schema) throw Bad("the schema is not " + Schema);
            var t = new CreateTxn {
                TxnId = s("txn_id"), ProjectKey = s("project_key"), SessionId = s("session_id"), RequestId = s("request_id"),
                Owner = s("owner"), Kind = s("kind"), FinalPath = s("final_path"), Phase = s("phase"), ScratchMeta = s("scratch_meta_sha256"),
                Guid = s("guid"), GlobalId = s("global_id"), Type = s("type"), TempFile = s("temp_sha256"), TempMeta = s("temp_meta_sha256"),
                FinalFile = s("final_sha256"), FinalMeta = s("final_meta_sha256"), StartedUtc = s("started_utc") };
            if (t.TxnId == null || !Protocol.Hex32.IsMatch(t.TxnId) || t.TxnId != fileTxnId) throw Bad("the transaction id is not its file name");
            if (t.ProjectKey != projectKey || !Protocol.Hex16.IsMatch(t.ProjectKey ?? "")) throw Bad("it names another Unity project");
            if (t.SessionId == null || !Protocol.Hex32.IsMatch(t.SessionId) || t.RequestId == null || !Protocol.Hex32.IsMatch(t.RequestId))
                throw Bad("the session or request id is not 32 hex");
            if (t.Owner == null || !Protocol.Owner.IsMatch(t.Owner)) throw Bad("the owner is not KIND:ID");
            if (t.Kind != AssetKinds.Material && t.Kind != AssetKinds.ScriptableObject) throw Bad("the kind is not a creatable asset kind");
            try { AssetPaths.CheckWritePath(t.FinalPath, t.Ext); }
            catch (Refusal) { throw Bad("the final path is not a valid asset path"); }
            if (t.PhaseIndex < 0) throw Bad("the phase is unknown");
            if (t.StartedUtc == null || !Utc.IsMatch(t.StartedUtc)) throw Bad("the start time is not a UTC timestamp");
            int phase = t.PhaseIndex;
            Action<string, string, int> hex = (name, value, from) =>
            {
                if (phase >= from ? value == null || !Hex64.IsMatch(value) : value != null) throw Bad(name + " does not match the phase");
            };
            hex("scratch_meta_sha256", t.ScratchMeta, 1);
            hex("temp_sha256", t.TempFile, 2);
            hex("temp_meta_sha256", t.TempMeta, 2);
            hex("final_sha256", t.FinalFile, 3);
            hex("final_meta_sha256", t.FinalMeta, 3);
            if (phase >= 2)
            {
                if (t.Guid == null || !Protocol.Hex32.IsMatch(t.Guid)) throw Bad("the GUID is not 32 hex");
                if (t.GlobalId != "GlobalObjectId_V1-3-" + t.Guid + "-" + (t.Kind == AssetKinds.Material ? "2100000" : "11400000") + "-0")
                    throw Bad("the GlobalObjectId is not the main object of the recorded GUID");
                if (t.Type == null || !ObjectIds.TypeId.IsMatch(t.Type)) throw Bad("the runtime type is not a type id");
            }
            else if (t.Guid != null || t.GlobalId != null || t.Type != null) throw Bad("identity is recorded before it was proven");
            return t;
        }
    }
}
