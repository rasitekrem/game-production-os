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
    app = Path(editor_executable).parents[2]            # .../Unity.app
    return json.loads((app / BUILT_IN / package / "package.json").read_text())["version"]


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
