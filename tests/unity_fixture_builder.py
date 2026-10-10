"""TEST-ONLY: generates disposable synthetic Unity projects for tests/test_unity_adapter.py.

No Unity project and no binary asset is committed: every project is written fresh into a temporary GPOS
project for each run. The projects are minimal: only packages bundled with the installed Editor (the
Unity Test Framework and two built-in modules), a ProjectVersion.txt naming exactly the probed Editor, and
a few deterministic tests. None of them contains a hanging or long-running test: the production adapter
always runs a platform's whole test set.

Kinds:
    pass     EditMode: 2 passing tests; PlayMode: 1 passing test that advances frames in Play Mode
    fail     EditMode: 1 passing, 1 failing test; PlayMode: 1 failing test
    zero     test assemblies with no tests
    compile  EditMode tests plus a script that does not compile
"""

import json
import math
import struct
import zlib
from pathlib import Path

BUILT_IN = "Contents/Resources/PackageManager/BuiltInPackages"

EDIT_ASMDEF = {"name": "Gpos.EditModeTests", "references": ["UnityEngine.TestRunner", "UnityEditor.TestRunner"],
               "includePlatforms": ["Editor"], "excludePlatforms": [], "allowUnsafeCode": False,
               "overrideReferences": True, "precompiledReferences": ["nunit.framework.dll"], "autoReferenced": False,
               "defineConstraints": ["UNITY_INCLUDE_TESTS"], "versionDefines": [], "noEngineReferences": False}
PLAY_ASMDEF = dict(EDIT_ASMDEF, name="Gpos.PlayModeTests", includePlatforms=[])

EDIT_TESTS = {
    "pass": """using NUnit.Framework;
public class GposEditModeTests
{
    [Test] public void Adds() { Assert.AreEqual(4, 2 + 2); }
    [Test] public void Concatenates() { Assert.AreEqual("ab", "a" + "b"); }
}
""",
    "fail": """using NUnit.Framework;
public class GposEditModeTests
{
    [Test] public void Adds() { Assert.AreEqual(4, 2 + 2); }
    [Test] public void FailsOnPurpose() { Assert.AreEqual(5, 2 + 2); }
}
""",
    "zero": """public class GposNoTestsHere { }
""",
}
EDIT_TESTS["compile"] = EDIT_TESTS["pass"]
PLAY_TESTS = {
    "pass": """using System.Collections;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;
public class GposPlayModeTests
{
    [UnityTest] public IEnumerator FramesAdvanceInPlayMode()
    {
        var go = new GameObject("GposProbe");
        int start = Time.frameCount;
        for (int i = 0; i < 5; i++) yield return null;
        Assert.IsTrue(Application.isPlaying);
        Assert.Greater(Time.frameCount, start);
        Object.Destroy(go);
    }
}
""",
    "fail": """using System.Collections;
using NUnit.Framework;
using UnityEngine.TestTools;
public class GposPlayModeTests
{
    [UnityTest] public IEnumerator FailsInPlayMode() { yield return null; Assert.Fail("on purpose"); }
}
""",
    "zero": """public class GposNoPlayTestsHere { }
""",
}
PLAY_TESTS["compile"] = PLAY_TESTS["pass"]
BROKEN = "public class GposBroken { void M() { int x = ; } }\n"


def bundled_version(editor_executable, package):
    """The version of a package bundled with the installed Editor (read from its own package.json)."""
    exe = Path(editor_executable)
    if exe.name.lower() == "unity.exe":                 # alpha.25, Windows: <...>\Editor\Unity.exe
        base = exe.parent / "Data" / "Resources" / "PackageManager" / "BuiltInPackages"
    else:
        base = exe.parents[2] / BUILT_IN                # .../Unity.app
    return json.loads((base / package / "package.json").read_text(encoding="utf-8"))["version"]


def manifest(editor_executable):
    return {"dependencies": {"com.unity.test-framework": bundled_version(editor_executable, "com.unity.test-framework"),
                             "com.unity.modules.imgui": "1.0.0", "com.unity.modules.jsonserialize": "1.0.0"}}


def make_project(path, kind, editor_version, editor_executable=None, dependencies=None):
    """A Unity project at `path` (created) of the given kind. `dependencies` overrides the manifest's."""
    path = Path(path)
    for sub in ("Assets/Tests/Editor", "Assets/Tests/Runtime", "Packages", "ProjectSettings"):
        (path / sub).mkdir(parents=True, exist_ok=True)
    (path / "Assets/Tests/Editor/Gpos.EditModeTests.asmdef").write_text(json.dumps(EDIT_ASMDEF, indent=2))
    (path / "Assets/Tests/Editor/GposEditModeTests.cs").write_text(EDIT_TESTS[kind])
    (path / "Assets/Tests/Runtime/Gpos.PlayModeTests.asmdef").write_text(json.dumps(PLAY_ASMDEF, indent=2))
    (path / "Assets/Tests/Runtime/GposPlayModeTests.cs").write_text(PLAY_TESTS[kind])
    if kind == "compile":
        (path / "Assets/Scripts").mkdir(exist_ok=True)
        (path / "Assets/Scripts/Broken.cs").write_text(BROKEN)
    deps = dependencies if dependencies is not None else (
        manifest(editor_executable)["dependencies"] if editor_executable else {"com.unity.test-framework": "1.7.0"})
    (path / "Packages/manifest.json").write_text(json.dumps({"dependencies": deps}, indent=2))
    (path / "ProjectSettings/ProjectVersion.txt").write_text(f"m_EditorVersion: {editor_version}\n")
    return path


# ---------------------------------------------------------------- Phase 2C-6B1: the Scene-authoring fixture

AUTHORING_SCRIPTS = {
    # every supported property kind, and the ones that are always refused
    "Assets/Authoring/AuthorProps.cs": """using System.Collections.Generic;
using UnityEngine;
public enum AuthorMode { Off, On, Auto }
[System.Flags] public enum AuthorFlags { None = 0, A = 1, B = 2, C = 4 }
public enum AuthorAlias { First = 1, Same = 1, Other = 2 }
[System.Serializable] public class AuthorNested { public int n; public string s; public Vector3 v; }
public interface IAuthorShape { }
[System.Serializable] public class AuthorCircle : IAuthorShape { public float r = 1; }
public class AuthorProps : MonoBehaviour
{
    public bool b; public sbyte i8; public short i16; public int i32; public long i64;
    public byte u8; public ushort u16; public uint u32; public ulong u64;
    public float f32; public double f64; public string s = "hi"; public AuthorMode mode; public AuthorFlags flags; public AuthorAlias alias;
    public Vector2 v2; public Vector3 v3; public Vector4 v4; public Vector2Int v2i; public Vector3Int v3i;
    public Rect rect; public RectInt ri; public Bounds bounds; public BoundsInt bi; public Color c = Color.white;
    public Quaternion q = Quaternion.identity; public LayerMask mask;
    public GameObject go; public Transform tr; public BoxCollider box; public Material mat; public AuthorClamp clamp;
    public AnimationCurve curve = AnimationCurve.Linear(0, 0, 1, 1); public Gradient gradient = new Gradient();
    public int[] arr = { 1, 2 }; public List<string> list = new List<string> { "a" }; public AuthorNested nested;
    [SerializeReference] public IAuthorShape shape = new AuthorCircle();
    public ExposedReference<GameObject> exposed; [HideInInspector] public int hidden; [SerializeField] private int priv;
    public char ch; public Hash128 h;
    public int Priv { get { return priv; } }
}
public class AuthorNoScript : MonoBehaviour { }
""",
    "Assets/Authoring/AuthorClamp.cs": """using UnityEngine;
public class AuthorClamp : MonoBehaviour
{
    public int value;
    void OnValidate() { if (value > 10) value = 10; }
}
""",
    "Assets/Authoring/AuthorDrift.cs": """using UnityEngine;
// Project code that is not idempotent: every OnValidate call changes serialized state.
public class AuthorDrift : MonoBehaviour
{
    public int value; public int validations;
    void OnValidate() { validations++; if (value > 10) value = 10; }
}
""",
    "Assets/Authoring/AuthorSingle.cs": "using UnityEngine;\n[DisallowMultipleComponent] public class AuthorSingle : MonoBehaviour { }\n",
    "Assets/Authoring/AuthorNeedsBox.cs":
        "using UnityEngine;\n[RequireComponent(typeof(BoxCollider))] public class AuthorNeedsBox : MonoBehaviour { }\n",
    "Assets/Authoring/AuthorChanging.cs": "using UnityEngine;\npublic class AuthorChanging : MonoBehaviour { }\n",
    "Assets/Authoring/AuthorAbstract.cs": "using UnityEngine;\npublic abstract class AuthorAbstract : MonoBehaviour { }\n",
    "Assets/Authoring/AuthorGeneric.cs": "using UnityEngine;\npublic class AuthorGeneric<T> : MonoBehaviour { }\n",
    "Assets/Authoring/AuthorObsolete.cs":
        "using UnityEngine;\n[System.Obsolete(\"fixture\")] public class AuthorObsolete : MonoBehaviour { }\n",
    "Assets/Authoring/AuthorHidden.cs": "using UnityEngine;\n[AddComponentMenu(\"\")] public class AuthorHidden : MonoBehaviour { }\n",
    "Assets/Authoring/AuthorInternal.cs": "using UnityEngine;\ninternal class AuthorInternal : MonoBehaviour { }\n",
    "Assets/Authoring/Editor/AuthorEditorOnly.cs": "using UnityEngine;\npublic class AuthorEditorOnly : MonoBehaviour { }\n",
    "Assets/AuthorA/AuthorA.asmdef": json.dumps({"name": "AuthorA"}),
    "Assets/AuthorA/AuthorDup.cs": "using UnityEngine;\npublic class AuthorDup : MonoBehaviour { public int a; }\n",
    "Assets/AuthorB/AuthorB.asmdef": json.dumps({"name": "AuthorB"}),
    "Assets/AuthorB/AuthorDup.cs": "using UnityEngine;\npublic class AuthorDup : MonoBehaviour { public int b; }\n",
}
AUTHORING_MODULES = ("com.unity.modules.physics", "com.unity.modules.physics2d")


def make_authoring_project(path, editor_version, editor_executable):
    """The `pass` project plus the Scene-authoring fixture scripts and the physics modules (for built-in
    components that conflict). Scenes and prefabs are created in the lab Editor by the testkit, never committed."""
    path = make_project(path, "pass", editor_version, editor_executable)
    for rel, text in AUTHORING_SCRIPTS.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text)
    manifest_path = path / "Packages/manifest.json"
    data = json.loads(manifest_path.read_text())
    data["dependencies"].update({m: "1.0.0" for m in AUTHORING_MODULES})
    manifest_path.write_text(json.dumps(data, indent=2))
    return path


# ---------------------------------------------------------------- Phase 2C-9.3b: the Windows live demo fixture

DEMO_SCENE = "Assets/Scenes/Demo.unity"
DEMO_NAME = "GposDemoRoot"
DEMO_POSITION, DEMO_ROTATION, DEMO_SCALE = (1.5, 2.0, -3.0), (0.0, 0.70710677, 0.0, 0.70710677), (2.0, 2.0, 2.0)
DEMO_PERSISTENCE_TEST = """using System.Linq;
using NUnit.Framework;
using UnityEditor.SceneManagement;
using UnityEngine;
// alpha.26: run by the batch plane after the live session: what the live Editor saved is on disk.
public class GposEditModeTests
{
    [Test] public void TheSavedDemoRootIsAnEmptyGameObjectWithItsTransform()
    {
        var scene = EditorSceneManager.OpenScene("%(scene)s", OpenSceneMode.Single);
        var all = Object.FindObjectsByType<GameObject>(FindObjectsInactive.Include, FindObjectsSortMode.None);
        var found = all.Where(g => g.name == "%(name)s").ToArray();
        Assert.AreEqual(1, found.Length, "exactly one %(name)s");
        var go = found[0];
        Assert.AreEqual(scene, go.scene, "in the demo Scene");
        Assert.IsNull(go.transform.parent, "a Scene root");
        Assert.AreEqual(1, go.GetComponents<Component>().Length, "an empty GameObject: only its Transform");
        Assert.AreEqual(1, scene.rootCount, "nothing else was added to the Scene");
        Assert.AreEqual(new Vector3(%(px)sf, %(py)sf, %(pz)sf), go.transform.localPosition);
        Assert.AreEqual(new Vector3(%(sx)sf, %(sy)sf, %(sz)sf), go.transform.localScale);
        Assert.Less(Quaternion.Angle(new Quaternion(%(rx)sf, %(ry)sf, %(rz)sf, %(rw)sf), go.transform.localRotation), 0.01f);
    }
}
""" % dict(scene=DEMO_SCENE, name=DEMO_NAME, px=DEMO_POSITION[0], py=DEMO_POSITION[1], pz=DEMO_POSITION[2],
           sx=DEMO_SCALE[0], sy=DEMO_SCALE[1], sz=DEMO_SCALE[2], rx=DEMO_ROTATION[0], ry=DEMO_ROTATION[1],
           rz=DEMO_ROTATION[2], rw=DEMO_ROTATION[3])


def make_live_demo_project(path, editor_version, editor_executable):
    """The `pass` project whose EditMode test checks, from disk, the GposDemoRoot a live session saved into the demo
    Scene. The Scene itself is made by the testkit (`demo-scene`) in the lab Editor before GPOS attaches."""
    path = make_project(path, "pass", editor_version, editor_executable)
    (path / "Assets/Tests/Editor/GposEditModeTests.cs").write_text(DEMO_PERSISTENCE_TEST)
    return path


# ---------------------------------------------------------------- Phase 2C-6B2A: the asset fixture

def png(width, height, rgba):
    """A small RGBA PNG, generated (no binary fixture is committed)."""
    rows = b"".join(b"\x00" + b"".join(bytes(rgba(x, y)) for x in range(width)) for y in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def wav(seconds=0.25, rate=22050):
    """A short 16-bit mono sine WAV, generated."""
    n = int(seconds * rate)
    data = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(n))
    return (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) + b"data" + struct.pack("<I", len(data)) + data)


CUBE_OBJ = """o FixtureCube
v -0.5 -0.5 0.5
v 0.5 -0.5 0.5
v -0.5 0.5 0.5
v 0.5 0.5 0.5
v -0.5 0.5 -0.5
v 0.5 0.5 -0.5
v -0.5 -0.5 -0.5
v 0.5 -0.5 -0.5
f 1 2 4 3
f 3 4 6 5
f 5 6 8 7
f 7 8 2 1
f 2 8 6 4
f 7 1 3 5
"""

TEST_SHADER = """Shader "GPOS/Test"
{
    Properties
    {
        _Color ("Color", Color) = (1, 1, 1, 1)
        [HideInInspector] _Tint ("Tint", Color) = (1, 1, 1, 1)
        _Amount ("Amount", Range(0, 1)) = 0.5
        _Count ("Count", Integer) = 1
        _Offset ("Offset", Vector) = (0, 0, 0, 0)
        _Scale ("Scale", Float) = 1
        _MainTex ("Texture", 2D) = "white" {}
        _Cube ("Cube", Cube) = "" {}
        _Vol ("Volume", 3D) = "" {}
        [PerRendererData] _PerR ("Per renderer", 2D) = "white" {}
    }
    SubShader
    {
        Pass
        {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"
            fixed4 _Color;
            float4 vert (float4 v : POSITION) : SV_POSITION { return UnityObjectToClipPos(v); }
            fixed4 frag () : SV_Target { return _Color; }
            ENDCG
        }
    }
}
"""

ASSET_SCRIPTS = {
    "Assets/AssetScripts/AssetRefs.cs": """using UnityEngine;
public class AssetRefs : MonoBehaviour
{
    public Material mat; public Texture tex; public Texture2D tex2d; public Sprite sprite; public AudioClip clip;
    public Mesh mesh; public GameObject prefab; public BoxCollider col; public GameConfig config; public Cubemap cube;
    public Object anything;
}
""",
    "Assets/AssetScripts/GameConfig.cs": """using UnityEngine;
[CreateAssetMenu(menuName = "GPOS/Game Config", fileName = "Config")]
public class GameConfig : ScriptableObject
{
    public int value; public float speed; public string title = "t"; public Material material; public Texture2D icon;
    public Sprite badge; public GameConfig next; [HideInInspector] public int hidden; public int[] arr = { 1 };
    public AnimationCurve curve = AnimationCurve.Linear(0, 0, 1, 1);
}
""",
    "Assets/AssetScripts/PlainData.cs": "using UnityEngine;\npublic class PlainData : ScriptableObject { public int value; }\n",
    "Assets/AssetScripts/CallbackData.cs": """using UnityEngine;
// Project code that runs on creation (OnEnable) and on every edit (OnValidate clamps).
[CreateAssetMenu]
public class CallbackData : ScriptableObject
{
    public int value; public int enables;
    void OnEnable() { enables++; }
    void OnValidate() { if (value > 10) value = 10; }
}
""",
    "Assets/AssetScripts/Changeable.cs": "using UnityEngine;\npublic class Changeable : ScriptableObject { public int x; }\n",
    "Assets/AssetScripts/AbstractData.cs": "using UnityEngine;\npublic abstract class AbstractData : ScriptableObject { }\n",
    "Assets/AssetScripts/GenericData.cs": "using UnityEngine;\npublic class GenericData<T> : ScriptableObject { }\n",
    "Assets/AssetScripts/Editor/EditorData.cs": "using UnityEngine;\n[CreateAssetMenu] public class EditorData : ScriptableObject { }\n",
    "Assets/AssetScripts/Editor/GposFixturePostprocessor.cs": """using System.IO;
using System.Linq;
using UnityEditor;
// Project code that runs on every import and move (TOOL_INHERENT for GPOS): it only logs the paths.
class GposFixturePostprocessor : AssetPostprocessor
{
    static void OnPostprocessAllAssets(string[] imported, string[] deleted, string[] moved, string[] movedFrom)
    {
        File.AppendAllText("Temp/gpos-postprocess.log", string.Join("\\n", imported.Concat(moved)) + "\\n");
    }
}
""",
    "Assets/Shaders/GposTest.shader": TEST_SHADER,
    "Assets/Models/cube.obj": CUBE_OBJ,
    "Packages/com.gpos.fixture-assets/package.json": json.dumps({"name": "com.gpos.fixture-assets", "version": "1.0.0",
                                                                 "displayName": "GPOS fixture assets",
                                                                 "unity": "6000.5"}, indent=2),
}
ASSET_MODULES = ("com.unity.modules.audio", "com.unity.modules.imageconversion")


def make_asset_project(path, editor_version, editor_executable):
    """The Scene-authoring project plus the asset fixture: textures, audio, a model, a test shader with every shader
    property kind, ScriptableObject types (creatable, editable only, Editor-only, abstract, generic), a logging
    AssetPostprocessor, and the embedded package com.gpos.fixture-assets (one texture, one in an Editor folder).
    Materials, prefabs, ScriptableObject assets and Scenes are made in the lab Editor by the testkit."""
    path = make_authoring_project(path, editor_version, editor_executable)
    for rel, text in ASSET_SCRIPTS.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text)
    art = path / "Assets/Art"
    art.mkdir(parents=True, exist_ok=True)
    (art / "checker.png").write_bytes(png(16, 16, lambda x, y: (255, 255, 255, 255) if (x // 4 + y // 4) % 2
                                          else (0, 0, 0, 255)))
    (art / "hero.png").write_bytes(png(16, 16, lambda x, y: (200, 60, 60, 255)))
    (path / "Assets/Audio").mkdir(parents=True, exist_ok=True)
    (path / "Assets/Audio/beep.wav").write_bytes(wav())
    package = path / "Packages/com.gpos.fixture-assets"
    for rel in ("Textures", "Materials", "Editor"):
        (package / rel).mkdir(parents=True, exist_ok=True)
    (package / "Textures/pkg.png").write_bytes(png(8, 8, lambda x, y: (0, 120, 255, 255)))
    (package / "Editor/editor.png").write_bytes(png(8, 8, lambda x, y: (0, 255, 0, 255)))
    manifest_path = path / "Packages/manifest.json"
    data = json.loads(manifest_path.read_text())
    data["dependencies"].update({m: "1.0.0" for m in ASSET_MODULES})
    manifest_path.write_text(json.dumps(data, indent=2))
    return path


# ---------------------------------------------------------------- Phase 2C-6B2B: the prefab fixture

PREFAB_SCRIPTS = {
    "Assets/PrefabScripts/PrefabRefs.cs": """using UnityEngine;
public class PrefabRefs : MonoBehaviour
{
    public GameObject other; public Transform child; public BoxCollider box; public Material mat; public Texture tex;
    public Texture2D tex2d; public Sprite sprite; public AudioClip clip; public Mesh mesh; public GameConfig config;
    public GameObject prefab; public BoxCollider prefabBox; public PhysicsMaterial physics; public Object anything;
    public int count; public float speed; public string title = "t";
}
""",
    "Assets/PrefabScripts/PrefabProbe.cs": """using System.IO;
using UnityEngine;
// Project code in a prefab: while Temp/gpos-probe-validate exists, every OnValidate call of an instance in a saved
// Scene changes serialized state (a prefab override); the prefab asset itself is left alone.
[ExecuteAlways]
public class PrefabProbe : MonoBehaviour
{
    public int validations; public int stamp; public int value; public int echo;
    void OnValidate()
    {
        if (File.Exists("Temp/gpos-probe-validate") && gameObject.scene.IsValid() && gameObject.scene.path.EndsWith(".unity")) validations++;
        // while Temp/gpos-probe-echo exists, echo is copied into the sibling PrefabRefs (project code changing another component)
        var refs = GetComponent<PrefabRefs>();
        if (File.Exists("Temp/gpos-probe-echo") && refs != null) refs.count = echo;
    }
}
""",
    "Assets/PrefabScripts/Editor/PrefabStamp.cs": """using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
// Project code on every prefab import: while Temp/gpos-probe-stamp exists, it stamps each imported PrefabProbe; while
// Temp/gpos-dirty-scenes exists, it marks every open Scene dirty after a prefab was imported.
class PrefabStamp : AssetPostprocessor
{
    void OnPostprocessPrefab(GameObject root)
    {
        if (!File.Exists("Temp/gpos-probe-stamp")) return;
        foreach (var p in root.GetComponentsInChildren<PrefabProbe>(true)) p.stamp++;
    }

    static void OnPostprocessAllAssets(string[] imported, string[] deleted, string[] moved, string[] movedFrom)
    {
        if (File.Exists("Temp/gpos-dirty-scenes") && imported.Any(p => p.EndsWith(".prefab"))) EditorSceneManager.MarkAllScenesDirty();
    }
}
""",
}


def make_prefab_project(path, editor_version, editor_executable):
    """The asset project plus the prefab fixture scripts: a component with every reviewed reference kind, a project
    script whose OnValidate changes state on demand and an AssetPostprocessor whose OnPostprocessPrefab does. Prefabs,
    Variants, nested and package prefabs and the Scene content are made in the lab Editor by the testkit."""
    path = make_asset_project(path, editor_version, editor_executable)
    for rel, text in PREFAB_SCRIPTS.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text)
    return path
