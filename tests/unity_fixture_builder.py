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
