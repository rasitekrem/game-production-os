// TEST-ONLY: tests of the asset part of the bridge's Unity-free core (Phase 2C-6B2A): asset id and write-path
// grammars, the kind table, the composite token, Material value rules, the Renderer slot rule, the ScriptableObject
// and shader catalog digests, the persistent asset-creation record and the asset commands of the protocol.
// Compiled and run with CoreTests.cs by tests/test_unity_live_bridge_core.py.
using System;
using System.Collections.Generic;
using System.Linq;

namespace Gpos.LiveBridge
{
    static partial class CoreTests
    {
        const string AG = "0123456789abcdef0123456789abcdef";

        // ------------------------------------------------------------ asset ids

        static void AssetIdsAcceptOnlyReviewedIdentifierTypes()
        {
            Equal(1, AssetIds.Check("GlobalObjectId_V1-1-" + AG + "-2800000-0"), "imported asset");
            Equal(3, AssetIds.Check("GlobalObjectId_V1-3-" + AG + "-2100000-0"), "source asset");
            Equal(4, AssetIds.Check("GlobalObjectId_V1-4-" + AssetIds.DefaultResourcesGuid + "-10202-0"), "default resources");
            Equal(4, AssetIds.Check("GlobalObjectId_V1-4-" + AssetIds.BuiltinExtraGuid + "-10303-0"), "built-in extra");
            Equal(1, AssetIds.Check("GlobalObjectId_V1-1-" + AG + "-18446744073709551615-0"), "ulong max file id");
            foreach (var bad in new[] {
                "GlobalObjectId_V1-2-" + AG + "-1-0", "GlobalObjectId_V1-0-" + AG + "-1-0", "GlobalObjectId_V1-5-" + AG + "-1-0",
                "GlobalObjectId_V1-1-" + AG + "-1-1", "GlobalObjectId_V1-1-" + AG + "-18446744073709551616-0",
                "GlobalObjectId_V1-1-" + ObjectIds.ZeroGuid + "-1-0", "GlobalObjectId_V1-4-" + AG + "-1-0",
                "GlobalObjectId_V1-1-" + AssetIds.DefaultResourcesGuid + "-1-0", "GlobalObjectId_V1-3-" + AssetIds.BuiltinExtraGuid + "-1-0",
                "GlobalObjectId_V1-1-" + AG.ToUpperInvariant() + "-1-0", "GlobalObjectId_V1-1-" + AG + "-1-0\n",
                "GlobalObjectId_V1-1-" + AG + "-1-0\r\n", " GlobalObjectId_V1-1-" + AG + "-1-0", "Assets/Materials/A.mat",
                "Library/PackageCache/com.x/A.mat", "12345", "", null })
                Equal("OBJECT_REFUSED", Refused(() => AssetIds.Check(bad)), "refused: " + bad);
            Check(AssetIds.IsAsset("GlobalObjectId_V1-3-" + AG + "-1-0") && !AssetIds.IsAsset("GlobalObjectId_V1-2-" + AG + "-1-0"), "IsAsset");
            AssetIds.CheckReference("GlobalObjectId_V1-2-" + AG + "-1-0");
            AssetIds.CheckReference("GlobalObjectId_V1-1-" + AG + "-1-0");
            Equal("OBJECT_REFUSED", Refused(() => AssetIds.CheckReference("GlobalObjectId_V1-1-" + AG + "-1-2")), "an asset reference has prefab id 0");
            Equal("SCENE_NOT_SAVED", Refused(() => AssetIds.CheckReference("GlobalObjectId_V1-2-" + ObjectIds.ZeroGuid + "-1-0")), "unsaved Scene");
            Equal(AG, AssetIds.Guid("GlobalObjectId_V1-3-" + AG + "-1-0"), "guid");
        }

        // ------------------------------------------------------------ write paths

        static void AssetWritePathsAreExactAndPlain()
        {
            Equal("Assets/Materials", AssetPaths.CheckWritePath("Assets/Materials/Hero.mat", ".mat"), "parent");
            Equal("Assets/A b/C (1)", AssetPaths.CheckWritePath("Assets/A b/C (1)/x-y_z.mat", ".mat"), "spaces and parentheses");
            Equal("Assets/Resources", AssetPaths.CheckWritePath("Assets/Resources/Config.asset", ".asset"), "Resources is a plain folder");
            AssetPaths.CheckWritePath("Assets/" + string.Join("/", Enumerable.Repeat("d", 16)) + "/m.mat", ".mat");
            foreach (var bad in new[] {
                "Packages/com.x/Hero.mat", "Library/Hero.mat", "/Assets/M/Hero.mat", "Assets/Hero.mat", "Assets/M/../Hero.mat",
                "Assets/./M/Hero.mat", "Assets//Hero.mat", "Assets/M/Hero.asset", "Assets/M/Hero.MAT", "Assets/M/Hero",
                "Assets/M/Hero.mat\n", "Assets/M/Hero\n.mat", "Assets/M/Hero\r\n.mat", "Assets/M\n/Hero.mat", "Assets/Editor/Hero.mat", "Assets/X/EDITOR/Hero.mat", "Assets/StreamingAssets/H.mat",
                "Assets/Editor Default Resources/H.mat", "Assets/.git/H.mat", "Assets/M~/H.mat", "Assets/M./H.mat",
                "Assets/GposAssetTxn-" + AG + "/H.mat", "Assets/gposassettxn-x/H.mat", "Assets/M/ Hero.mat", "Assets/M/Hero .mat",
                "Assets/M/He:ro.mat", "Assets/M/" + new string('x', 65) + ".mat", "Assets\\M\\Hero.mat", "Assets/M/.mat",
                "Assets/" + string.Join("/", Enumerable.Repeat("d", 17)) + "/m.mat", "assets/M/Hero.mat", "", null })
                Equal("ASSET_PATH_INVALID", Refused(() => AssetPaths.CheckWritePath(bad, ".mat")), "refused: " + bad);
            Equal("ASSET_PATH_INVALID", Refused(() => AssetPaths.CheckWritePath("Assets/M/Hero.prefab", ".prefab")), "only .mat and .asset are written");
            Equal("Assets/GposAssetTxn-" + AG, AssetPaths.ScratchFolder(AG), "scratch folder");
            Equal("Assets/GposAssetTxn-" + AG + "/Hero.mat", AssetPaths.TempPath(AG, "Assets/Materials/Hero.mat", ".mat"), "temp keeps the final file name");
            Equal("CREATE_INCOMPLETE", Refused(() => AssetPaths.ScratchFolder("../../x")), "a transaction id is hex");
            Equal("CREATE_INCOMPLETE", Refused(() => AssetPaths.ScratchFolder(AG + "\n")), "whole string");
        }

        // ------------------------------------------------------------ kinds, bounds and the token

        static void AssetKindsAndTokens()
        {
            Equal(9, AssetKinds.All.Length, "kinds");
            Check(!AssetKinds.Findable(AssetKinds.PrefabComponent) && AssetKinds.Findable(AssetKinds.Material), "findable");
            Equal(".mat", AssetKinds.Extension(AssetKinds.Material), "material extension");
            Equal(".asset", AssetKinds.Extension(AssetKinds.ScriptableObject), "SO extension");
            Equal("", AssetKinds.Extension(AssetKinds.Texture), "textures are never written");
            Equal(64, AssetKinds.Digest().Length, "kinds digest");
            string t = AssetToken.Of("id", "Assets/a.mat", "T", "m", false, "f", "x");
            Equal(32, t.Length, "token length");
            foreach (var other in new[] { AssetToken.Of("id2", "Assets/a.mat", "T", "m", false, "f", "x"), AssetToken.Of("id", "Assets/b.mat", "T", "m", false, "f", "x"),
                                          AssetToken.Of("id", "Assets/a.mat", "U", "m", false, "f", "x"), AssetToken.Of("id", "Assets/a.mat", "T", "n", false, "f", "x"),
                                          AssetToken.Of("id", "Assets/a.mat", "T", "m", true, "f", "x"), AssetToken.Of("id", "Assets/a.mat", "T", "m", false, "g", "x"),
                                          AssetToken.Of("id", "Assets/a.mat", "T", "m", false, "f", "y") })
                Check(other != t, "every field changes the token");
            Check(AssetToken.Of("a", "bc", "T", "m", false, "f", "x") != AssetToken.Of("ab", "c", "T", "m", false, "f", "x"), "fields are length-prefixed");
            AssetBounds.CheckQuery(null);
            AssetBounds.CheckQuery(new string('x', 64));
            Equal("BAD_ARGUMENTS", Refused(() => AssetBounds.CheckQuery(new string('x', 65))), "query bound");
            Equal("BAD_ARGUMENTS", Refused(() => AssetBounds.CheckQuery("a\nb")), "printable query");
        }

        // ------------------------------------------------------------ Material values

        static void MaterialValuesFollowTheShaderDeclaration()
        {
            Equal("color", MaterialRules.KindOf("Color"), "color");
            Equal("int", MaterialRules.KindOf("Int"), "int");
            Equal(null, MaterialRules.KindOf("Keyword"), "unknown");
            Equal("HIDDEN", MaterialRules.FlagProblem(true, false, false), "hidden");
            Equal("PER_RENDERER_DATA", MaterialRules.FlagProblem(false, true, false), "per renderer");
            Equal("NOT_EDITABLE", MaterialRules.FlagProblem(false, false, true), "non-modifiable texture");
            Equal(null, MaterialRules.FlagProblem(false, false, false), "writable");
            Equal(4, MaterialRules.Parse("color", L(1.0, 0.5, 2.0, 1.0), 0, 0).Floats.Length, "HDR color");
            Equal(0.25f, MaterialRules.Parse("range", 0.25, 0, 1).Float, "in range");
            Equal("VALUE_INVALID", Refused(() => MaterialRules.Parse("range", 1.5, 0, 1)), "out of the declared range");
            Equal("VALUE_INVALID", Refused(() => MaterialRules.Parse("range", -0.1, 0, 1)), "below the declared range");
            Equal("VALUE_INVALID", Refused(() => MaterialRules.Parse("float", 1e39, 0, 0)), "float32 range");
            Equal("VALUE_INVALID", Refused(() => MaterialRules.Parse("color", L(1.0, 1.0, 1.0), 0, 0)), "arity");
            Equal(-7, MaterialRules.Parse("int", -7.0, 0, 0).Int, "int");
            Equal("VALUE_INVALID", Refused(() => MaterialRules.Parse("int", 1.5, 0, 0)), "whole");
            Equal("VALUE_INVALID", Refused(() => MaterialRules.Parse("int", 2147483648.0, 0, 0)), "int32");
            Equal(null, MaterialRules.Parse("texture", null, 0, 0).TextureId, "clear");
            Equal("OBJECT_REFUSED", Refused(() => MaterialRules.Parse("texture", "GlobalObjectId_V1-2-" + AG + "-1-0", 0, 0)), "a Scene object is no texture");
            Equal("VALUE_INVALID", Refused(() => MaterialRules.Parse("texture", 5.0, 0, 0)), "texture type");
            Equal("PROPERTY_UNSUPPORTED", Refused(() => MaterialRules.Parse("keyword", true, 0, 0)), "no keyword emulation");
            Check(MaterialRules.DimensionMatches("Tex2D", "Tex2D") && MaterialRules.DimensionMatches("Any", "Cube"), "dimension match");
            Check(!MaterialRules.DimensionMatches("Tex2D", "Cube") && !MaterialRules.DimensionMatches("Cube", "Tex2D"), "dimension mismatch");
            Check(!MaterialRules.DimensionMatches("Any", "None") && !MaterialRules.DimensionMatches("Tex3D", "Unknown"), "no texture");
        }

        // ------------------------------------------------------------ Renderer slots

        static void RendererSlotsAreReplacedNeverResized()
        {
            Equal(RendererSlots.Replace, RendererSlots.Operation(0, 1), "replace 0");
            Equal(RendererSlots.Replace, RendererSlots.Operation(2, 3), "replace 2");
            Equal(RendererSlots.CreateFirst, RendererSlots.Operation(0, 0), "the first slot of a renderer without slots");
            Equal("PROPERTY_UNSUPPORTED", Refused(() => RendererSlots.Operation(1, 1)), "append");
            Equal("PROPERTY_UNSUPPORTED", Refused(() => RendererSlots.Operation(1, 0)), "slot 1 of none");
            Equal("PROPERTY_UNSUPPORTED", Refused(() => RendererSlots.Operation(5, 2)), "beyond");
            Equal("BAD_ARGUMENTS", Refused(() => RendererSlots.Operation(8, 9)), "the fixed slot bound");
            Equal("BAD_ARGUMENTS", Refused(() => RendererSlots.Operation(-1, 1)), "negative");
        }

        // ------------------------------------------------------------ catalog digests

        static ScriptableEntry So(string id, bool creatable = true, string menu = "M")
        {
            return new ScriptableEntry { TypeId = "A::" + id, Assembly = "A", FullName = id, Name = id, Namespace = "", ScriptMapped = true,
                                         Creatable = creatable, MenuName = menu, FileName = "F" };
        }

        static void CatalogDigestsCoverEveryField()
        {
            var a = new[] { So("X"), So("Y") };
            string d = AssetCatalogDigest.ScriptableObjects(a);
            Equal(64, d.Length, "digest length");
            Equal(d, AssetCatalogDigest.ScriptableObjects(a.Reverse()), "order does not matter");
            Check(d != AssetCatalogDigest.ScriptableObjects(new[] { So("X", false), So("Y") }), "creatable changes it");
            Check(d != AssetCatalogDigest.ScriptableObjects(new[] { So("X", true, "N"), So("Y") }), "the menu changes it");
            var mapped = So("X");
            mapped.ScriptMapped = false;
            Check(d != AssetCatalogDigest.ScriptableObjects(new[] { mapped, So("Y") }), "the script mapping changes it");
            var file = So("X");
            file.FileName = "G";
            Check(d != AssetCatalogDigest.ScriptableObjects(new[] { file, So("Y") }), "the file name changes it");
            Check(d != AssetCatalogDigest.ScriptableObjects(new[] { So("X") }), "membership changes it");
            Func<float, string, bool, ShaderEntry> shader = (max, dim, hidden) => new ShaderEntry {
                Id = "s", Name = "S", Properties = new List<ShaderProperty> {
                    new ShaderProperty { Name = "_A", Type = "Range", RangeMin = 0, RangeMax = max },
                    new ShaderProperty { Name = "_T", Type = "Texture", Dimension = dim, HideInInspector = hidden } } };
            string s = AssetCatalogDigest.Shaders(new[] { shader(1, "Tex2D", false) });
            Equal(s, AssetCatalogDigest.Shaders(new[] { shader(1, "Tex2D", false) }), "stable");
            Check(s != AssetCatalogDigest.Shaders(new[] { shader(2, "Tex2D", false) }), "a range limit changes it");
            Check(s != AssetCatalogDigest.Shaders(new[] { shader(1, "Cube", false) }), "a texture dimension changes it");
            Check(s != AssetCatalogDigest.Shaders(new[] { shader(1, "Tex2D", true) }), "a flag changes it");
            var p = new ShaderProperty { Name = "_T", Type = "Texture", Dimension = "Tex2D", PerRendererData = true };
            Equal("PER_RENDERER_DATA", p.Refusal, "property refusal");
            Equal(false, p.ToData()["writable"], "listed as not writable");
            Equal("TYPE_UNSUPPORTED", new ShaderProperty { Name = "_K", Type = "Keyword" }.Refusal, "unsupported type");
        }

        // ------------------------------------------------------------ the persistent creation record

        const string TxnKey = "0123456789abcdef";
        const string Txn = "abcdefabcdefabcdefabcdefabcdefab";

        static CreateTxn Record(string phase)
        {
            var t = new CreateTxn { TxnId = Txn, ProjectKey = TxnKey, SessionId = Sid, RequestId = Id(7), Owner = "AGENT:w1",
                                    Kind = AssetKinds.Material, FinalPath = "Assets/Materials/Hero.mat", Phase = phase,
                                    StartedUtc = "2026-09-27T12:00:00.1234567Z" };
            int i = t.PhaseIndex;
            if (i >= 1) t.ScratchMeta = new string('1', 64);
            if (i >= 2)
            {
                t.Guid = AG;
                t.GlobalId = "GlobalObjectId_V1-3-" + AG + "-2100000-0";
                t.Type = "UnityEngine.CoreModule::UnityEngine.Material";
                t.TempFile = new string('2', 64);
                t.TempMeta = new string('3', 64);
            }
            if (i >= 3) { t.FinalFile = new string('2', 64); t.FinalMeta = new string('3', 64); }
            return t;
        }

        static string Mangle(string json, string key, string value)
        {
            var d = (Dictionary<string, object>)Json.Parse(json);
            d[key] = value;
            return Json.Write(d);
        }

        static void CreationRecordsAreStrictAndNeverAPath()
        {
            foreach (var phase in CreateTxn.Phases)
            {
                var t = Record(phase);
                var back = CreateTxn.Parse(t.Write(), Txn, TxnKey);
                Equal(phase, back.Phase, "round trip " + phase);
                Equal("Assets/GposAssetTxn-" + Txn + "/Hero.mat", back.TempPath, "the temp path is derived");
                Equal("Assets/GposAssetTxn-" + Txn, back.ScratchFolder, "the scratch folder is derived");
            }
            string good = Record(CreateTxn.TempProven).Write();
            Equal("CREATE_INCOMPLETE", Refused(() => CreateTxn.Parse(good, Id(9), TxnKey)), "the id is the file name");
            Equal("CREATE_INCOMPLETE", Refused(() => CreateTxn.Parse(good, Txn, "fedcba9876543210")), "another project");
            var bad = new List<string> {
                "{\"schema\":1,\"schema\":2}", "[]", good.Replace("}", ",\"extra\":1}"), good.Replace("\"schema\"", "\"Schema\""), new string(' ', 5000),
                Mangle(good, "final_path", "/etc/passwd"), Mangle(good, "final_path", "Assets/../x.mat"), Mangle(good, "final_path", "Packages/p/x.mat"),
                Mangle(good, "final_path", "Assets/M/Hero.mat\n"), Mangle(good, "final_path", "Assets/M/Hero.asset"), Mangle(good, "phase", "DONE"),
                Mangle(good, "kind", "TEXTURE"), Mangle(good, "owner", "nobody"), Mangle(good, "session_id", "x"),
                Mangle(good, "global_id", "GlobalObjectId_V1-3-" + AG + "-11400000-0"), Mangle(good, "global_id", "GlobalObjectId_V1-3-" + Id(3) + "-2100000-0"),
                Mangle(good, "temp_sha256", "abc"), Mangle(good, "started_utc", "yesterday"), Mangle(good, "type", "Material"),
                Mangle(good, "schema", "gpos.unity.live-bridge.asset-create-txn/2"), Mangle(good, "txn_id", Txn + "\n") };
            string prepared = Record(CreateTxn.Prepared).Write();
            bad.Add(Mangle(prepared, "guid", AG));                     // identity recorded before it was proven
            bad.Add(Mangle(prepared, "scratch_meta_sha256", new string('1', 64)));
            bad.Add(Mangle(Record(CreateTxn.FinalProven).Write(), "final_sha256", null));
            foreach (var text in bad)
                Equal("CREATE_INCOMPLETE", Refused(() => CreateTxn.Parse(text, Txn, TxnKey)), "refused: " + (text.Length > 70 ? text.Substring(0, 70) : text));
            Check(!Record(CreateTxn.FinalProven).Write().Contains("GposAssetTxn"), "the record holds no derived path");
        }

        // ------------------------------------------------------------ protocol

        static void AssetCommandsHaveExactArguments()
        {
            var specs = new Dictionary<string, string[]> {
                { "asset-types", new[] { "catalog", "query", "page" } },
                { "asset-find", new[] { "kind", "source", "query", "page" } },
                { "asset-inspect", new[] { "asset", "path_prefix", "page" } },
                { "create-material", new[] { "path", "shader", "expected_shader_catalog_digest" } },
                { "set-material-property", new[] { "material", "property", "kind", "value", "expected_asset_token" } },
                { "create-scriptable-object", new[] { "path", "type_id", "expected_so_catalog_digest" } },
                { "set-asset-property", new[] { "asset", "path", "kind", "value", "expected_asset_token" } } };
            int n = 5000;
            foreach (var kv in specs)
            {
                string args = "{" + string.Join(",", kv.Value.Select(k => "\"" + k + "\":null")) + "}";
                Equal(kv.Key, Parse(Id(++n), AuthorReq(Id(n), kv.Key, args)).Command, kv.Key);
                Check(Protocol.IsAuthoring(kv.Key) && Protocol.IsAsset(kv.Key), kv.Key + " is an asset command");
                Check(!Protocol.ChangesScene(kv.Key), kv.Key + " changes no Scene");
                Equal(!kv.Key.StartsWith("asset-", StringComparison.Ordinal), Protocol.ChangesAssets(kv.Key), kv.Key + " mutating");
                string extra = "{" + string.Join(",", kv.Value.Concat(new[] { "filter" }).Select(k => "\"" + k + "\":null")) + "}";
                Equal("BAD_ARGUMENTS", Code(() => Parse(Id(++n), AuthorReq(Id(n), kv.Key, extra))), kv.Key + " extra");
                Equal("MALFORMED_REQUEST", Code(() => Parse(Id(++n), Req(Id(n), kv.Key, args))), kv.Key + " needs the session");
            }
            Check(!Protocol.IsAsset("set-renderer-material") && Protocol.ChangesScene("set-renderer-material"), "the renderer command is a Scene change");
            foreach (var generic in new[] { "asset-database", "refresh", "save-assets", "delete-asset", "move-asset", "import-asset", "create-folder",
                                            "set-importer", "apply-prefab", "create-prefab", "reserialize" })
                Equal("UNKNOWN_COMMAND", Code(() => Parse(Id(++n), AuthorReq(Id(n), generic, "{}"))), generic);
        }
    }
}
