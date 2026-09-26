// TEST-ONLY: tests of the Scene-authoring part of the bridge's Unity-free core (Phase 2C-6B1): identifier grammars,
// tokens, the catalog digest, the property write allowlist and value validation, the authoring busy rule and the
// authoring commands of the protocol. Compiled and run with CoreTests.cs by tests/test_unity_live_bridge_core.py.
using System;
using System.Collections.Generic;
using System.Linq;

namespace Gpos.LiveBridge
{
    static partial class CoreTests
    {
        const string G = "0123456789abcdef0123456789abcdef";

        static string Refused(Action a) { return Code(a); }

        static PropertyValue P(string kind, object value) { return PropertyRules.Parse(kind, value); }

        static List<object> L(params object[] items) { return items.ToList(); }

        // ------------------------------------------------------------ identifiers

        static void AuthoringIdsAcceptOnlySavedSceneObjects()
        {
            Equal(G, ObjectIds.SceneGuid("GlobalObjectId_V1-2-" + G + "-1234-0"), "scene object");
            Equal(G, ObjectIds.SceneGuid("GlobalObjectId_V1-2-" + G + "-18446744073709551615-18446744073709551615"), "ulong max");
            Equal("OBJECT_REFUSED", Refused(() => ObjectIds.SceneGuid("GlobalObjectId_V1-1-" + G + "-1234-0")), "asset");
            Equal("OBJECT_REFUSED", Refused(() => ObjectIds.SceneGuid("GlobalObjectId_V1-3-" + G + "-1234-0")), "other type");
            Equal("OBJECT_REFUSED", Refused(() => ObjectIds.SceneGuid("GlobalObjectId_V1-2-" + G + "-18446744073709551616-0")), "overflow");
            Equal("OBJECT_REFUSED", Refused(() => ObjectIds.SceneGuid("GlobalObjectId_V1-2-" + G.ToUpperInvariant() + "-1-0")), "upper hex");
            Equal("OBJECT_REFUSED", Refused(() => ObjectIds.SceneGuid("GlobalObjectId_V1-2-" + G + "-1-0 ")), "trailing");
            Equal("OBJECT_REFUSED", Refused(() => ObjectIds.SceneGuid("12345")), "instance id");
            Equal("OBJECT_REFUSED", Refused(() => ObjectIds.SceneGuid("World/A/B")), "hierarchy path");
            Equal("OBJECT_REFUSED", Refused(() => ObjectIds.SceneGuid(null)), "null");
            Equal("SCENE_NOT_SAVED", Refused(() => ObjectIds.SceneGuid("GlobalObjectId_V1-2-" + ObjectIds.ZeroGuid + "-1234-0")), "unsaved scene");
            Equal("SCENE_NOT_SAVED", Refused(() => ObjectIds.SceneGuid("GlobalObjectId_V1-0-" + ObjectIds.ZeroGuid + "-0-0")), "null id");
        }

        static void AuthoringScenePathsNamesAndTokens()
        {
            ObjectIds.CheckScenePath("Assets/Scenes/Main.unity");
            ObjectIds.CheckScenePath("Assets/A b/Level 1.unity");
            Equal("SCENE_NOT_SAVED", Refused(() => ObjectIds.CheckScenePath("")), "unsaved");
            foreach (var bad in new[] { "Main.unity", "Packages/p/Main.unity", "Assets/Main.prefab", "Assets/../x.unity", "Assets//x.unity", "Assets/a\\b.unity", "/Assets/x.unity", "Assets/x.unity\n" })
                Equal("OBJECT_REFUSED", Refused(() => ObjectIds.CheckScenePath(bad)), bad);
            ObjectIds.CheckName("A name");
            Equal("VALUE_INVALID", Refused(() => ObjectIds.CheckName("")), "empty");
            Equal("VALUE_INVALID", Refused(() => ObjectIds.CheckName(new string('x', 129))), "long");
            Equal("VALUE_INVALID", Refused(() => ObjectIds.CheckName("a\nb")), "control");
            ObjectIds.CheckToken(G, "t");
            Equal("BAD_ARGUMENTS", Refused(() => ObjectIds.CheckToken(G + "0", "t")), "long token");
            Check(ObjectIds.TypeId.IsMatch("UnityEngine.PhysicsModule::UnityEngine.BoxCollider"), "type id");
            Check(ObjectIds.TypeId.IsMatch("Assembly-CSharp::Outer+Inner"), "nested type id");
            Check(!ObjectIds.TypeId.IsMatch("BoxCollider"), "bare name");
            Check(!ObjectIds.TypeId.IsMatch("A::List`1[System.Int32]"), "generic");
        }

        static void AuthoringGrammarsMatchTheWholeString()
        {
            foreach (var end in new[] { "\n", "\r\n" })
            {
                Equal("OBJECT_REFUSED", Refused(() => ObjectIds.SceneGuid("GlobalObjectId_V1-2-" + G + "-1-0" + end)), "object id" + end);
                Equal("OBJECT_REFUSED", Refused(() => ObjectIds.CheckScenePath("Assets/Scenes/Main.unity" + end)), "scene" + end);
                Equal("BAD_ARGUMENTS", Refused(() => ObjectIds.CheckToken(G + end, "t")), "token" + end);
                Check(!ObjectIds.TypeId.IsMatch("A::B" + end), "type id" + end);
                Check(!ObjectIds.Digest.IsMatch(G + G + end), "digest" + end);
                Equal("PATH_UNSUPPORTED", PropertyRules.PathProblem("nested.speed" + end), "path" + end);
                Equal(null, PropertyRules.PPtrType("PPtr<$GameObject>" + end), "PPtr" + end);
                Equal("VALUE_INVALID", Refused(() => P("int64", "-5" + end)), "int64" + end);
                Equal("VALUE_INVALID", Refused(() => P("uint64", "5" + end)), "uint64" + end);
                Equal("OBJECT_REFUSED", Refused(() => P("object", "GlobalObjectId_V1-2-" + G + "-1-0" + end)), "reference" + end);
                Check(!Protocol.Hex32.IsMatch(G + end) && !Protocol.Hex16.IsMatch(G.Substring(0, 16) + end), "hex" + end);
                Check(!Protocol.Owner.IsMatch("AGENT:w1" + end), "owner" + end);
                Equal("MALFORMED_REQUEST", Code(() => Parse(Id(900), Req(Id(900), "inspect", "{}", Sid + end))), "session id" + end);
                Equal("MALFORMED_REQUEST", Code(() => Parse(Id(901), Req(Id(901), "status", "{}", null, "AGENT:w1" + end))), "request owner" + end);
                Equal("MALFORMED_REQUEST", Code(() => Parse(Id(902), Req(Id(902), "status", "{}", null, "AGENT:w1", Boot + end))), "boot id" + end);
                string escaped = end == "\n" ? "\\n" : "\\r\\n";
                string timed = Req(Id(903)).Replace("Z\",\"start_deadline_utc\"", "Z" + escaped + "\",\"start_deadline_utc\"");
                Check(timed != Req(Id(903)), "the timestamp was altered");
                Equal("MALFORMED_REQUEST", Code(() => Parse(Id(903), timed)), "issued_utc" + end);
            }
            Equal(G, ObjectIds.SceneGuid("GlobalObjectId_V1-2-" + G + "-1-0"), "valid id");
            Check(Protocol.Owner.IsMatch("AGENT:w1") && ObjectIds.TypeId.IsMatch("A::B"), "valid forms");
        }

        // ------------------------------------------------------------ tokens

        static void AuthoringTokensAreCanonical()
        {
            string a = new TokenBuilder("d").Field("ab").Field("c").Finish();
            string b = new TokenBuilder("d").Field("a").Field("bc").Finish();
            Check(a != b, "fields are length-prefixed");
            Equal(32, a.Length, "32 hex");
            Equal(a, new TokenBuilder("d").Field("ab").Field("c").Finish(), "deterministic");
            Check(new TokenBuilder("d1").Field("x").Finish() != new TokenBuilder("d2").Field("x").Finish(), "domains separate");
            Equal(TokenBuilder.FloatBits(0f), TokenBuilder.FloatBits(-0f), "negative zero is zero");
            Equal("3f800000", TokenBuilder.FloatBits(1f), "binary32 bits");
            Check(TokenBuilder.FloatBits(0.1f) != TokenBuilder.FloatBits(0.100000009f), "adjacent floats differ");
            Equal(64, new TokenBuilder("d").FinishFull().Length, "full digest");
            Equal("1:d;3:abc;", new TokenBuilder("d").Field("abc").Text, "wire form");
            Equal("1:d;1:1;2:-5;", new TokenBuilder("d").Bool(true).Int(-5).Text, "bool and int");
            Equal("1:d;0:;", new TokenBuilder("d").Field(null).Text, "null is empty");
        }

        static CatalogEntry Entry(string id, bool single = false, bool editMode = false, params string[] requires)
        {
            return new CatalogEntry { TypeId = id, Assembly = id.Split(new[] { "::" }, StringSplitOptions.None)[0], FullName = id.Split(new[] { "::" }, StringSplitOptions.None)[1],
                                      Name = id.Split(new[] { "::" }, StringSplitOptions.None)[1], Namespace = "", Kind = "SCRIPT",
                                      DisallowMultiple = single, RunsInEditMode = editMode, Requires = requires.ToList() };
        }

        static void AuthoringCatalogDigestCoversMetadata()
        {
            string baseline = CatalogDigest.Of(new[] { Entry("A::X"), Entry("A::Y") });
            Equal(64, baseline.Length, "64 hex");
            Equal(baseline, CatalogDigest.Of(new[] { Entry("A::Y"), Entry("A::X") }), "entry order does not matter");
            Check(baseline != CatalogDigest.Of(new[] { Entry("A::X", single: true), Entry("A::Y") }), "DisallowMultipleComponent changes the digest");
            Check(baseline != CatalogDigest.Of(new[] { Entry("A::X", false, false, "E::Box"), Entry("A::Y") }), "RequireComponent changes the digest");
            Check(baseline != CatalogDigest.Of(new[] { Entry("A::X", false, true), Entry("A::Y") }), "ExecuteAlways changes the digest");
            Check(CatalogDigest.Of(new[] { Entry("A::X", false, false, "E::Box") }) != CatalogDigest.Of(new[] { Entry("A::X", false, false, "E::Sphere") }),
                  "which component is required is part of the digest");
            Equal(CatalogDigest.Of(new[] { Entry("A::X", false, false, "E::B", "E::A") }),
                  CatalogDigest.Of(new[] { Entry("A::X", false, false, "E::A", "E::B", "E::A") }), "requirements are sorted and de-duplicated");
            var native = Entry("A::X");
            native.Kind = "NATIVE";
            Check(baseline != CatalogDigest.Of(new[] { native, Entry("A::Y") }), "the kind is part of the digest");
            Check(baseline != CatalogDigest.Of(new[] { Entry("A::X") }), "a removed type changes the digest");
        }

        // ------------------------------------------------------------ the property allowlist

        static void AuthoringPropertyPathsAreDefaultDeny()
        {
            Equal(null, PropertyRules.PathProblem("speed"), "plain");
            Equal(null, PropertyRules.PathProblem("settings.inner.speed"), "nested");
            Equal(null, PropertyRules.PathProblem("a.b.c.d.e.f"), "six segments");
            Equal("PATH_UNSUPPORTED", PropertyRules.PathProblem("a.b.c.d.e.f.g"), "seven segments");
            foreach (var bad in new[] { "", "list.Array.data[0]", "a..b", ".a", "a.", "1a", "a b", "a-b", "<P>k__BackingField", new string('a', 257) })
                Equal("PATH_UNSUPPORTED", PropertyRules.PathProblem(bad), bad);
            foreach (var denied in new[] { "m_Script", "m_GameObject", "m_Name", "m_ObjectHideFlags", "m_EditorHideFlags", "m_PrefabInstance",
                                           "m_PrefabAsset", "m_CorrespondingSourceObject", "m_EditorClassIdentifier", "nested.m_Script", "m_Father", "m_Children" })
                Equal("DENIED", PropertyRules.PathProblem(denied), denied);
            Equal(null, PropertyRules.PathProblem("m_Enabled"), "m_Enabled is decided by the Editor code (Behaviour only)");
            Check(PropertyRules.Ancestors("a.b.c").SequenceEqual(new[] { "a", "a.b" }), "ancestors");
            Equal(0, PropertyRules.Ancestors("a").Count, "no ancestors");
        }

        static void AuthoringKindsMapExactlyOneUnityType()
        {
            var expected = new Dictionary<string, string[]> {
                { "bool", new[] { "Boolean", "bool" } }, { "int8", new[] { "Integer", "sbyte" } }, { "int16", new[] { "Integer", "short" } },
                { "int32", new[] { "Integer", "int" } }, { "int64", new[] { "Integer", "long" } }, { "uint8", new[] { "Integer", "byte" } },
                { "uint16", new[] { "Integer", "ushort" } }, { "uint32", new[] { "Integer", "uint" } }, { "uint64", new[] { "Integer", "ulong" } },
                { "float32", new[] { "Float", "float" } }, { "float64", new[] { "Float", "double" } }, { "string", new[] { "String", "string" } },
                { "enum", new[] { "Enum", "Enum" } }, { "vector2", new[] { "Vector2", "Vector2" } }, { "vector3", new[] { "Vector3", "Vector3" } },
                { "vector4", new[] { "Vector4", "Vector4" } }, { "vector2int", new[] { "Vector2Int", "Vector2Int" } },
                { "vector3int", new[] { "Vector3Int", "Vector3Int" } }, { "rect", new[] { "Rect", "Rect" } }, { "rectint", new[] { "RectInt", "RectInt" } },
                { "bounds", new[] { "Bounds", "Bounds" } }, { "boundsint", new[] { "BoundsInt", "BoundsInt" } }, { "color", new[] { "Color", "Color" } },
                { "quaternion", new[] { "Quaternion", "Quaternion" } }, { "layermask", new[] { "LayerMask", "LayerMask" } },
                { "object", new[] { "ObjectReference", "PPtr<$GameObject>" } } };
            foreach (var kv in expected) Equal(kv.Key, PropertyRules.KindOf(kv.Value[0], kv.Value[1]), kv.Key);
            Equal(PropertyRules.Kinds.Count, expected.Count, "every kind is covered");
            foreach (var refused in new[] { new[] { "Integer", "SInt8" }, new[] { "Character", "char" }, new[] { "Generic", "vector" }, new[] { "AnimationCurve", "AnimationCurve" },
                                            new[] { "Gradient", "Gradient" }, new[] { "ManagedReference", "managedReference<X>" }, new[] { "ExposedReference", "ExposedReference`1" },
                                            new[] { "Hash128", "Hash128" }, new[] { "ArraySize", "int" }, new[] { "FixedBufferSize", "int" }, new[] { "ObjectReference", "vector" } })
                Equal(null, PropertyRules.KindOf(refused[0], refused[1]), refused[0] + "/" + refused[1]);
            Equal("GameObject", PropertyRules.PPtrType("PPtr<$GameObject>"), "script field");
            Equal("Transform", PropertyRules.PPtrType("PPtr<Transform>"), "built-in field");
            Equal(null, PropertyRules.PPtrType("PPtr<$List`1>"), "not a simple type");
        }

        static void AuthoringValuesAreValidatedBeforeUnity()
        {
            Equal(-128L, P("int8", -128.0).Long, "int8 min");
            Equal("VALUE_INVALID", Refused(() => P("int8", 128.0)), "int8 max");
            Equal("VALUE_INVALID", Refused(() => P("uint8", -1.0)), "uint8 negative (Unity would wrap)");
            Equal("VALUE_INVALID", Refused(() => P("uint8", 300.0)), "uint8 300 (Unity would clamp)");
            Equal(65535L, P("uint16", 65535.0).Long, "uint16 max");
            Equal("VALUE_INVALID", Refused(() => P("int32", 5e9)), "int32 5e9 (Unity would saturate)");
            Equal("VALUE_INVALID", Refused(() => P("int32", 1.5)), "not whole");
            Equal("VALUE_INVALID", Refused(() => P("int32", "5")), "not a number");
            Equal("VALUE_INVALID", Refused(() => P("int32", true)), "bool is not a number");
            Equal(4294967295L, P("uint32", 4294967295.0).Long, "uint32 max");
            Equal("VALUE_INVALID", Refused(() => P("uint32", -1.0)), "uint32 -1 (Unity would store 0)");
            Equal(long.MinValue, P("int64", "-9223372036854775808").Long, "int64 min");
            Equal("VALUE_INVALID", Refused(() => P("int64", "9223372036854775808")), "int64 overflow");
            Equal("VALUE_INVALID", Refused(() => P("int64", 5.0)), "int64 as a JSON number (lossy) refused");
            Equal("VALUE_INVALID", Refused(() => P("int64", "05")), "leading zero");
            Equal(ulong.MaxValue, P("uint64", "18446744073709551615").ULong, "uint64 max");
            Equal("VALUE_INVALID", Refused(() => P("uint64", "-5")), "uint64 negative (Unity would wrap)");
            Equal("VALUE_INVALID", Refused(() => P("uint64", "18446744073709551616")), "uint64 overflow");
            Equal(0.1f, P("float32", 0.1).Float, "float32");
            Equal("VALUE_INVALID", Refused(() => P("float32", 1e39)), "float32 1e39 (Unity would store infinity)");
            Equal("VALUE_INVALID", Refused(() => P("float32", double.NaN)), "NaN");
            Equal("VALUE_INVALID", Refused(() => P("float64", double.PositiveInfinity)), "infinity");
            Equal(1e300, P("float64", 1e300).Double, "float64 range");
            Equal("VALUE_INVALID", Refused(() => P("string", new string('x', 4097))), "long string");
            Equal("VALUE_INVALID", Refused(() => P("string", "a\ud800b")), "lone surrogate");
            Equal("a😀b", P("string", "a😀b").String, "surrogate pair");
            Equal("VALUE_INVALID", Refused(() => P("bool", 1.0)), "bool");
            Equal("VALUE_INVALID", Refused(() => P("vector3", L(1.0, 2.0))), "vector arity");
            Equal("VALUE_INVALID", Refused(() => P("vector3", L(1.0, 2.0, 1e39))), "vector float32 range");
            Equal("VALUE_INVALID", Refused(() => P("vector2int", L(1.0, 2147483648.0))), "int vector range");
            var q = P("quaternion", L(0.0, 0.0, 0.0, 1.00005)).Floats;
            Check(Math.Abs(q[3] - 1f) < 1e-7, "a near-unit quaternion is normalized");
            Equal("VALUE_INVALID", Refused(() => P("quaternion", L(0.0, 0.0, 0.0, 2.0))), "non-unit quaternion (Unity would accept it)");
            Equal("VALUE_INVALID", Refused(() => P("quaternion", L(0.0, 0.0, 0.0, 0.0))), "zero quaternion");
            Equal("VALUE_INVALID", Refused(() => P("bounds", L(L(0.0, 0.0, 0.0), L(1.0, -1.0, 1.0)))), "negative extents");
            Equal(6, P("boundsint", L(L(1.0, 2.0, 3.0), L(4.0, 5.0, 6.0))).Ints.Length, "boundsint");
            Equal(4294967295L, P("layermask", 4294967295.0).Long, "layermask bits");
            Equal(null, P("object", null).String, "null reference");
            Equal("OBJECT_REFUSED", Refused(() => P("object", "GlobalObjectId_V1-1-" + G + "-1-0")), "asset reference");
            Equal("SCENE_NOT_SAVED", Refused(() => P("object", "GlobalObjectId_V1-2-" + ObjectIds.ZeroGuid + "-1-0")), "unsaved-scene reference");
            Equal("VALUE_INVALID", Refused(() => P("object", 5.0)), "reference is not a number");
            Equal("VALUE_INVALID", Refused(() => P("enum", 1.0)), "enum by index refused: by exact name only");
            Equal("PROPERTY_UNSUPPORTED", Refused(() => P("char", 65.0)), "unknown kind");
        }

        // ------------------------------------------------------------ busy rule and protocol

        static void AuthoringOnlyInEditModeWithNothingPending()
        {
            Equal(null, Transitions.AuthoringBusy("EDIT", false), "edit mode");
            foreach (var phase in new[] { "PLAYING", "PAUSED", "ENTERING_PLAYMODE", "EXITING_PLAYMODE", "COMPILING", "UPDATING", "RELOADING", "QUITTING" })
                Equal(phase, Transitions.AuthoringBusy(phase, false), phase);
            Equal("PENDING_OPERATION", Transitions.AuthoringBusy("EDIT", true), "a pending transition");
            Equal(null, Transitions.Busy("PLAYING", false), "Play Mode commands still run while playing");
        }

        static string AuthorReq(string id, string command, string args) { return Req(id, command, args, Sid); }

        static void AuthoringCommandsHaveExactArguments()
        {
            var specs = new Dictionary<string, string[]> {
                { "object-inspect", new[] { "object", "scene", "children_limit" } },
                { "component-types", new[] { "query", "page" } },
                { "properties", new[] { "component", "path_prefix", "page" } },
                { "create-gameobject", new[] { "scene", "name", "parent", "sibling", "local_position", "local_rotation", "local_scale", "expected_parent_token", "expected_scene_roots_token" } },
                { "delete-gameobject", new[] { "object", "expected_subtree_token" } },
                { "set-parent", new[] { "object", "parent", "keep_world", "sibling", "expected_object_token", "expected_transform_token", "expected_old_parent_token",
                                        "expected_old_scene_roots_token", "expected_new_parent_token", "expected_new_scene_roots_token",
                                        "expected_transform_chain_token", "expected_new_parent_chain_token" } },
                { "set-gameobject", new[] { "object", "name", "active", "tag", "layer", "static_flags", "expected_object_token" } },
                { "set-transform", new[] { "object", "local_position", "local_rotation", "local_scale", "expected_transform_token" } },
                { "add-component", new[] { "object", "type_id", "expected_object_token", "expected_catalog_digest" } },
                { "remove-component", new[] { "component", "expected_component_token", "expected_object_token" } },
                { "set-property", new[] { "component", "path", "kind", "value", "expected_component_token" } },
                { "save-scene", new[] { "scene" } } };
            int n = 1000;
            foreach (var kv in specs)
            {
                string args = "{" + string.Join(",", kv.Value.Select(k => "\"" + k + "\":null")) + "}";
                var r = Parse(Id(++n), AuthorReq(Id(n), kv.Key, args));
                Equal(kv.Key, r.Command, kv.Key);
                Check(Protocol.IsAuthoring(kv.Key), kv.Key + " is authoring");
                Check(!Protocol.ChangesEditorState(kv.Key), kv.Key + " is not a Play Mode command");
                Equal(kv.Key != "object-inspect" && kv.Key != "component-types" && kv.Key != "properties", Protocol.ChangesScene(kv.Key), kv.Key + " mutating");
                string missing = "{" + string.Join(",", kv.Value.Skip(1).Select(k => "\"" + k + "\":null")) + "}";
                Equal("BAD_ARGUMENTS", Code(() => Parse(Id(++n), AuthorReq(Id(n), kv.Key, missing))), kv.Key + " missing");
                string extra = "{" + string.Join(",", kv.Value.Concat(new[] { "method" }).Select(k => "\"" + k + "\":null")) + "}";
                Equal("BAD_ARGUMENTS", Code(() => Parse(Id(++n), AuthorReq(Id(n), kv.Key, extra))), kv.Key + " extra");
                Equal("MALFORMED_REQUEST", Code(() => Parse(Id(++n), Req(Id(n), kv.Key, args))), kv.Key + " needs the session");
            }
            Check(!Protocol.IsAuthoring("inspect") && !Protocol.IsAuthoring("pause"), "the alpha.16 commands are not authoring");
            Equal("gpos.unity.live/2", Protocol.Name, "protocol");
            Equal("1.1.0", Protocol.BridgeVersion, "version");
            foreach (var generic in new[] { "execute", "eval", "invoke", "menu", "reflect", "save-scene-as", "apply-prefab", "set-asset" })
                Equal("UNKNOWN_COMMAND", Code(() => Parse(Id(++n), AuthorReq(Id(n), generic, "{}"))), generic);
        }
    }
}
