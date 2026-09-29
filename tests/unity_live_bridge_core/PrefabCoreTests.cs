// TEST-ONLY: tests of the prefab part of the bridge's Unity-free core (Phase 2C-6B2B): the persistent prefab id
// grammar and the isolated-contents id mapping, the new-prefab path, the bounds, the scope reasons, the source
// reference rule, the PREFAB creation record (schema /2) and the prefab commands of the protocol.
// Compiled and run with CoreTests.cs by tests/test_unity_live_bridge_core.py.
using System;
using System.Collections.Generic;
using System.Linq;

namespace Gpos.LiveBridge
{
    static partial class CoreTests
    {
        const string PG = "fedcba9876543210fedcba9876543210";

        static void PrefabIdsArePersistentTypeOneOnly()
        {
            Equal(PG, PrefabIds.Guid("GlobalObjectId_V1-1-" + PG + "-4211300-0"), "root or object");
            Equal("18446744073709551615", PrefabIds.FileId("GlobalObjectId_V1-1-" + PG + "-18446744073709551615-0"), "ulong max");
            foreach (var bad in new[] {
                "GlobalObjectId_V1-2-" + PG + "-4211300-0", "GlobalObjectId_V1-3-" + PG + "-2100000-0", "GlobalObjectId_V1-4-" + AssetIds.DefaultResourcesGuid + "-1-0",
                "GlobalObjectId_V1-1-" + PG + "-4211300-7", "GlobalObjectId_V1-1-" + PG + "-4211300-0\n", "GlobalObjectId_V1-1-" + PG.ToUpperInvariant() + "-1-0",
                "GlobalObjectId_V1-1-" + ObjectIds.ZeroGuid + "-1-0", "GlobalObjectId_V1-1-" + AssetIds.BuiltinExtraGuid + "-1-0",
                "GlobalObjectId_V1-1-" + PG + "-18446744073709551616-0", " GlobalObjectId_V1-1-" + PG + "-1-0", "Assets/Prefabs/A.prefab", "", null })
                Equal("OBJECT_REFUSED", Refused(() => PrefabIds.Guid(bad)), "refused: " + bad);
            Check(PrefabIds.IsPersistent("GlobalObjectId_V1-1-" + PG + "-5-0") && !PrefabIds.IsPersistent("GlobalObjectId_V1-2-" + PG + "-5-0"), "IsPersistent");
            // an isolated-contents copy maps to exactly one persistent object; anything else maps to none
            Equal("GlobalObjectId_V1-1-" + PG + "-77-0", PrefabIds.FromContents(2, PG, 77, 0, PG), "contents copy");
            Check(PrefabIds.FromContents(1, PG, 77, 0, PG) == null, "not a contents identity");
            Check(PrefabIds.FromContents(2, AG, 77, 0, PG) == null, "another prefab");
            Check(PrefabIds.FromContents(2, PG, 77, 3, PG) == null, "a nested instance's object");
            Check(PrefabIds.FromContents(2, PG, 0, 0, PG) == null, "an object without a file id (not saved yet)");
            Check(PrefabIds.IsRecordRoot("GlobalObjectId_V1-1-" + PG + "-9-0", PG), "record root");
            Check(!PrefabIds.IsRecordRoot("GlobalObjectId_V1-1-" + AG + "-9-0", PG), "record root of another GUID");
            Check(!PrefabIds.IsRecordRoot("GlobalObjectId_V1-3-" + PG + "-9-0", PG), "record root of another type");
            Check(!PrefabIds.IsRecordRoot(null, PG) && !PrefabIds.IsRecordRoot("GlobalObjectId_V1-1-" + PG + "-9-0", null), "nulls");
        }

        static void PrefabPathsAreNewPlainPrefabFilesBelowAssets()
        {
            Equal("Assets/Prefabs", PrefabPaths.CheckWritePath("Assets/Prefabs/Crate.prefab"), "parent");
            Equal("Assets/A b/C (1)", AssetPaths.CheckPrefabPath("Assets/A b/C (1)/x-y_z.prefab"), "plain names");
            foreach (var bad in new[] {
                "Packages/com.x/Crate.prefab", "Library/Crate.prefab", "/Assets/P/Crate.prefab", "Assets/Crate.prefab", "Assets/P/../Crate.prefab",
                "Assets/P/Crate.asset", "Assets/P/Crate.mat", "Assets/P/Crate.Prefab", "Assets/P/Crate.prefab\n", "Assets/Editor/Crate.prefab",
                "Assets/GposAssetTxn-" + AG + "/Crate.prefab", "Assets/P/ Crate.prefab", "Assets/.hidden/Crate.prefab", "", null })
                Equal("ASSET_PATH_INVALID", Refused(() => PrefabPaths.CheckWritePath(bad)), "refused: " + bad);
            // the asset commands never accept a prefab path, and the kind table gives prefabs no authorable extension
            Equal("ASSET_PATH_INVALID", Refused(() => AssetPaths.CheckWritePath("Assets/P/Crate.prefab", ".prefab")), "asset path rule");
            Equal("", AssetKinds.Extension(AssetKinds.Prefab), "no generic prefab extension");
            Equal("", AssetKinds.Extension(AssetKinds.PrefabComponent), "no generic prefab component extension");
            Equal(".prefab", PrefabPaths.Extension, "prefab extension");
        }

        static void PrefabBoundsNeverTruncate()
        {
            PrefabBounds.Check(PrefabBounds.MaxGameObjects, PrefabBounds.MaxComponents, PrefabBounds.MaxDepth, "x");
            Equal("PREFAB_LIMIT", Refused(() => PrefabBounds.Check(PrefabBounds.MaxGameObjects + 1, 1, 0, "x")), "GameObjects");
            Equal("PREFAB_LIMIT", Refused(() => PrefabBounds.Check(1, PrefabBounds.MaxComponents + 1, 0, "x")), "components");
            Equal("PREFAB_LIMIT", Refused(() => PrefabBounds.Check(1, 1, PrefabBounds.MaxDepth + 1, "x")), "depth");
            Equal(256, PrefabBounds.MaxGameObjects, "the reviewed bound");
            Equal(100000, PrefabBounds.MaxDependentScan, "the reviewed Scene scan bound");
        }

        static void PrefabScopeIsRegularAssetsWithoutNesting()
        {
            Equal(0, PrefabScope.Reasons(PrefabScope.Regular, AssetKinds.Assets, false, false, false).Count, "the mutable scope");
            Equal("VARIANT", PrefabScope.Reasons(PrefabScope.Variant, AssetKinds.Assets, false, false, false).Single(), "variant");
            Equal("MODEL", PrefabScope.Reasons(PrefabScope.Model, AssetKinds.Assets, false, false, false).Single(), "model");
            Equal("PACKAGE", PrefabScope.Reasons(PrefabScope.Regular, AssetKinds.Package, false, false, false).Single(), "package");
            Equal("NESTED_PRESENT", PrefabScope.Reasons(PrefabScope.Regular, AssetKinds.Assets, true, false, false).Single(), "nested");
            Equal("MISSING_SCRIPT", PrefabScope.Reasons(PrefabScope.Regular, AssetKinds.Assets, false, true, false).Single(), "missing script");
            Equal("HIDDEN_CONTENT", PrefabScope.Reasons(PrefabScope.Regular, AssetKinds.Assets, false, false, true).Single(), "hidden");
            Equal("VARIANT,PACKAGE,NESTED_PRESENT", string.Join(",", PrefabScope.Reasons(PrefabScope.Variant, AssetKinds.Package, true, false, false)), "order");
            Check(PrefabScope.Refusal(new List<string>()) == null, "no refusal");
            Equal("PREFAB_REFUSED", PrefabScope.Refusal(new List<string> { "NESTED_PRESENT" }).Code, "refusal code");
        }

        static void SourceReferencesAreInsideReviewedOrStructural()
        {
            foreach (var ok in new[] { PrefabReferences.Null, PrefabReferences.Inside, PrefabReferences.ReviewedAsset })
                Check(PrefabReferences.Refusal("other", false, ok) == null, "allowed: " + ok);
            Check(PrefabReferences.Refusal("m_Script", false, PrefabReferences.OwnScript) == null, "a component's own script");
            Check(PrefabReferences.Refusal("m_Father", true, PrefabReferences.SceneOutside) == null, "the root Transform's parent is cut");
            Check(PrefabReferences.Refusal("m_PrefabInstance", false, PrefabReferences.Null) == null, "no prefab link");
            Equal("SCENE_OUTSIDE", PrefabReferences.Refusal("m_Father", false, PrefabReferences.SceneOutside), "an inner Transform's parent outside");
            Equal("SCENE_OUTSIDE", PrefabReferences.Refusal("other", false, PrefabReferences.SceneOutside), "outside the subtree");
            Equal("SCENE_OUTSIDE", PrefabReferences.Refusal("list.Array.data[0]", false, PrefabReferences.SceneOutside), "inside an array");
            Equal("UNSUPPORTED_ASSET", PrefabReferences.Refusal("physics", false, PrefabReferences.UnsupportedAsset), "unsupported asset");
            Equal("OTHER_SCRIPT", PrefabReferences.Refusal("script", false, PrefabReferences.OtherScript), "a script elsewhere");
            Equal("SCRIPT", PrefabReferences.Refusal("other", false, PrefabReferences.OwnScript), "a script outside m_Script");
            Equal("SCRIPT", PrefabReferences.Refusal("m_Script", false, PrefabReferences.OtherScript), "another script");
            Equal("MISSING_SCRIPT", PrefabReferences.Refusal("m_Script", false, PrefabReferences.Null), "a missing script");
            Equal("PREFAB_LINK", PrefabReferences.Refusal("m_PrefabAsset", false, PrefabReferences.ReviewedAsset), "a prefab link");
            Equal("PREFAB_LINK", PrefabReferences.Refusal("m_CorrespondingSourceObject", false, PrefabReferences.Inside), "a source link");
            Equal("UNKNOWN_REFERENCE", PrefabReferences.Refusal("other", false, "SOMETHING"), "unknown");
            Equal("UNKNOWN_REFERENCE", PrefabReferences.Refusal(null, false, PrefabReferences.Null), "no path");
            Check(PrefabReferences.IsLink("m_PrefabInstance") && PrefabReferences.IsLink("m_PrefabAsset.x") && !PrefabReferences.IsLink("m_Father"), "links");
        }

        static CreateTxn PrefabRecord(string phase)
        {
            var t = new CreateTxn { TxnId = Txn, ProjectKey = TxnKey, SessionId = Sid, RequestId = Id(7), Owner = "AGENT:w1",
                                    Kind = AssetKinds.Prefab, FinalPath = "Assets/Prefabs/Crate.prefab", Phase = phase,
                                    StartedUtc = "2026-09-28T12:00:00.1234567Z" };
            int i = t.PhaseIndex;
            if (i >= 1) t.ScratchMeta = new string('1', 64);
            if (i >= 2)
            {
                t.Guid = PG;
                t.GlobalId = "GlobalObjectId_V1-1-" + PG + "-6613297564218905913-0";
                t.Type = "UnityEngine.CoreModule::UnityEngine.GameObject";
                t.TempFile = new string('2', 64);
                t.TempMeta = new string('3', 64);
            }
            if (i >= 3) { t.FinalFile = new string('2', 64); t.FinalMeta = new string('3', 64); }
            return t;
        }

        static void PrefabCreationRecordsAreSchemaTwoAndStrict()
        {
            foreach (var phase in CreateTxn.Phases)
            {
                var t = PrefabRecord(phase);
                string text = t.Write();
                Check(text.Contains("\"gpos.unity.live-bridge.asset-create-txn/2\""), "a prefab record is schema /2");
                var back = CreateTxn.Parse(text, Txn, TxnKey);
                Equal(phase, back.Phase, "round trip " + phase);
                Equal("Assets/GposAssetTxn-" + Txn + "/Crate.prefab", back.TempPath, "the temp path is derived");
            }
            Check(Record(CreateTxn.Prepared).Write().Contains("\"gpos.unity.live-bridge.asset-create-txn/1\""), "a Material record stays schema /1");
            string good = PrefabRecord(CreateTxn.TempProven).Write();
            foreach (var text in new[] {
                Mangle(good, "schema", "gpos.unity.live-bridge.asset-create-txn/1"),                     // a prefab in a /1 record
                Mangle(Record(CreateTxn.Prepared).Write(), "schema", "gpos.unity.live-bridge.asset-create-txn/2"),   // a Material in /2
                Mangle(good, "schema", "gpos.unity.live-bridge.asset-create-txn/3"),
                Mangle(good, "final_path", "Assets/Prefabs/Crate.mat"), Mangle(good, "final_path", "Packages/p/Crate.prefab"),
                Mangle(good, "final_path", "Assets/Editor/Crate.prefab"), Mangle(good, "global_id", "GlobalObjectId_V1-1-" + AG + "-5-0"),
                Mangle(good, "global_id", "GlobalObjectId_V1-2-" + PG + "-5-0"), Mangle(good, "global_id", "GlobalObjectId_V1-1-" + PG + "-0-0"),
                Mangle(good, "global_id", "GlobalObjectId_V1-1-" + PG + "-5-1"), Mangle(good, "kind", "PREFAB_COMPONENT"),
                Mangle(good, "kind", "MODEL"), Mangle(PrefabRecord(CreateTxn.Prepared).Write(), "guid", PG) })
                Equal("CREATE_INCOMPLETE", Refused(() => CreateTxn.Parse(text, Txn, TxnKey)), "refused: " + (text.Length > 90 ? text.Substring(0, 90) : text));
        }

        static void PrefabCommandsHaveExactArgumentsAndClassification()
        {
            var specs = new Dictionary<string, string[]> {
                { "prefab-inspect", new[] { "prefab", "component", "path_prefix", "page" } },
                { "prefab-instance-inspect", new[] { "object", "page" } },
                { "create-prefab", new[] { "source", "path", "expected_subtree_token" } },
                { "instantiate-prefab", new[] { "prefab", "scene", "parent", "sibling", "local_position", "local_rotation", "local_scale",
                                                "expected_prefab_token", "expected_parent_token", "expected_scene_roots_token" } },
                { "set-prefab-gameobject", new[] { "object", "name", "active", "tag", "layer", "static_flags", "expected_prefab_token" } },
                { "set-prefab-transform", new[] { "object", "local_position", "local_rotation", "local_scale", "expected_prefab_token" } },
                { "add-prefab-component", new[] { "object", "type_id", "expected_prefab_token", "expected_catalog_digest" } },
                { "remove-prefab-component", new[] { "component", "expected_prefab_token" } },
                { "set-prefab-property", new[] { "component", "path", "kind", "value", "expected_prefab_token" } } };
            int n = 9000;
            foreach (var kv in specs)
            {
                string args = "{" + string.Join(",", kv.Value.Select(k => "\"" + k + "\":null")) + "}";
                Equal(kv.Key, Parse(Id(++n), AuthorReq(Id(n), kv.Key, args)).Command, kv.Key);
                Check(Protocol.IsAuthoring(kv.Key) && Protocol.IsPrefab(kv.Key) && !Protocol.IsAsset(kv.Key), kv.Key + " is a prefab command");
                Check(!Protocol.ChangesEditorState(kv.Key), kv.Key + " is not a Play Mode command");
                bool read = kv.Key.EndsWith("-inspect", StringComparison.Ordinal);
                Equal(kv.Key == "instantiate-prefab", Protocol.ChangesScene(kv.Key), kv.Key + " changes a Scene");
                Equal(!read && kv.Key != "instantiate-prefab", Protocol.ChangesAssets(kv.Key), kv.Key + " changes a prefab file");
                string extra = "{" + string.Join(",", kv.Value.Concat(new[] { "apply" }).Select(k => "\"" + k + "\":null")) + "}";
                Equal("BAD_ARGUMENTS", Code(() => Parse(Id(++n), AuthorReq(Id(n), kv.Key, extra))), kv.Key + " extra");
                Equal("MALFORMED_REQUEST", Code(() => Parse(Id(++n), Req(Id(n), kv.Key, args))), kv.Key + " needs the session");
            }
            // the 32 earlier commands keep their classification
            foreach (var c in new[] { "create-gameobject", "delete-gameobject", "set-parent", "set-gameobject", "set-transform", "add-component",
                                      "remove-component", "set-property", "save-scene", "set-renderer-material" })
                Check(Protocol.ChangesScene(c) && !Protocol.ChangesAssets(c) && !Protocol.IsPrefab(c), c + " stays a Scene command");
            foreach (var c in new[] { "create-material", "set-material-property", "create-scriptable-object", "set-asset-property" })
                Check(!Protocol.ChangesScene(c) && Protocol.ChangesAssets(c) && !Protocol.IsPrefab(c), c + " stays an asset command");
            Equal(14 + 13 + 7 + 9 + 3, Protocol.Commands.Count(), "the closed allowlist: 14 session and Play Mode, 13 Scene, 7 asset, 9 prefab and (bridge 1.4.0) 3 source commands");
            foreach (var deferred in new[] { "apply-prefab", "apply-prefab-override", "revert-prefab", "revert-prefab-override", "unpack-prefab",
                                             "create-variant", "connect-prefab", "save-as-prefab-and-connect", "open-prefab-stage", "close-prefab-stage",
                                             "save-prefab-stage", "create-prefab-child", "delete-prefab-child", "prefab-utility", "edit-model-prefab" })
                Equal("UNKNOWN_COMMAND", Code(() => Parse(Id(++n), AuthorReq(Id(n), deferred, "{}"))), deferred);
        }
    }
}
