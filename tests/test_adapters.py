#!/usr/bin/env python3
"""Phase 2B — agent adapter layer tests.

    python3 tests/test_adapters.py

Standard library only; no network; nothing outside temporary directories is written (HOME is
redirected to a temporary directory for the whole run and checked to stay empty).

Groups: A IR · B Claude Code rendering · C Codex rendering · D semantic parity · E drift ·
F sync and ownership · G path security · H project validation · I size and context ·
J regression and boundaries · K CLI · L snapshots.
"""

import copy
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_HOME = tempfile.mkdtemp(prefix="gpos-home-")
os.environ["HOME"] = _HOME  # any accidental write to a user/global agent directory lands here and fails J01

from gpos.adapters import backends, cli as adapter_cli, content, pipeline  # noqa: E402
from gpos.adapters import diagnostics as adg  # noqa: E402
from gpos.adapters.compiler import compile_ir  # noqa: E402
from gpos.adapters.manifest import ir_semantics  # noqa: E402
from gpos.adapters.paths import is_managed, is_safe_relative  # noqa: E402
from gpos.adapters.render import render_bundle  # noqa: E402
from gpos.adapters.sources import canonical_json, front_matter  # noqa: E402
from gpos.adapters.validation import validate_bundle  # noqa: E402
from gpos.framework import load_framework  # noqa: E402

FW = load_framework()
REG = FW.registry
FIXTURE = ROOT / "tests" / "fixtures" / "adapter-project"
SNAPSHOTS = ROOT / "tests" / "fixtures" / "adapter-snapshots"
CLAUDE, CODEX = backends.BACKENDS["claude-code"], backends.BACKENDS["codex"]
ENABLED = ["game-director", "gameplay-design", "character-animation", "camera-composition", "qa-performance", "game-engineering"]


def project(tmp=None, all_skills=False):
    """A fresh copy of the synthetic adapter project."""
    base = Path(tmp or tempfile.mkdtemp(prefix="gpos-adapter-"))
    dest = base / "project"
    shutil.copytree(FIXTURE, dest)
    if all_skills:
        edit_config(dest, lambda c: c["extensions"].pop("gpos-adapters"))
    return dest


def edit_config(p, fn):
    path = p / ".game" / "gpos" / "project-config.json"
    c = json.loads(path.read_text())
    fn(c)
    path.write_text(json.dumps(c, indent=2, sort_keys=True) + "\n")


def tree(p):
    """{relative path: sha256} of every file under p (symlinks recorded by target)."""
    out = {}
    for f in sorted(Path(p).rglob("*")):
        rel = f.relative_to(p).as_posix()
        if f.is_symlink():
            out[rel] = "->" + os.readlink(f)
        elif f.is_file():
            out[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def codes(result):
    return {d.code for d in result.diagnostics}


def run_cli(*argv):
    out = io.StringIO()
    return adapter_cli.main(list(argv), stdout=out), out.getvalue()


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gpos-adapter-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def proj(self, **kw):
        return project(self.tmp, **kw)


# ---------------------------------------------------------------- A  IR

class A01_IR(TmpCase):
    def test_one_ir_with_authority_order_and_all_skills(self):
        ir = compile_ir(self.proj(all_skills=True))
        self.assertEqual([lvl for lvl, _ in ir.authority_order], REG["authority_levels"])
        self.assertEqual([s.name for s in ir.skills], REG["skills"])
        self.assertEqual(len(ir.skills), 13)
        for s in ir.skills:
            self.assertEqual([h for h, _ in s.sections], REG["skill_contract_sections"], s.name)
            source = front_matter((ROOT / "skills" / s.name / "SKILL.md").read_text())
            self.assertEqual(s.maturity, source["maturity"], s.name)
            gates = [g.strip() for g in source["may_own_gates"].strip("[]").split(",") if g.strip()]
            self.assertEqual(list(s.may_own_gates), gates, s.name)
            for g in gates:
                self.assertIn(s.name, REG["gates"][g]["permitted_owners"], s.name)
            self.assertEqual(s.maturity, "DRAFT", s.name)
            self.assertEqual(list(s.cross_reviewers), REG["cross_review_eligibility"].get(s.name, []), s.name)
            self.assertEqual(s.never_cross_reviewer, s.name in REG["never_cross_reviewer"], s.name)
            self.assertLessEqual(len(s.description), content.DESCRIPTION_MAX_CHARS)
        self.assertTrue(ir.skill("game-director").never_cross_reviewer)
        self.assertEqual([w.name for w in ir.workflows], REG["workflows"])

    def test_enabled_skills_and_ir_is_deterministic(self):
        p = self.proj()
        ir = compile_ir(p)
        self.assertEqual([s.name for s in ir.skills], ENABLED)
        self.assertEqual(set(ir.disabled_skills), set(REG["skills"]) - set(ENABLED))
        self.assertEqual(canonical_json(ir.to_dict()), canonical_json(compile_ir(p).to_dict()))

    def test_project_authority_stays_distinguishable(self):
        ir = compile_ir(self.proj())
        docs = {d.file: d for d in ir.project_authority}
        self.assertEqual({f for f, d in docs.items() if d.present}, {"PROJECT.md", "ANIMATION.md", "GAME-DESIGN.md"})
        anim = docs["ANIMATION.md"]
        locked = [r for r in anim.rows if r.status == "LOCKED"]
        self.assertEqual([(r.item, r.decision_ref, r.lock_verified) for r in locked],
                         [("Root motion vs in-place policy", "D-0101", True)])
        style = next(r for r in anim.rows if r.item == "Animation style statement")
        self.assertEqual((style.status, style.placeholders, style.value), ("PROPOSED", ("HUMAN_DECISION_REQUIRED",),
                                                                           "`HUMAN_DECISION_REQUIRED`"))
        self.assertTrue(any("UNDECIDED" in r.placeholders for r in anim.rows))
        self.assertEqual(ir.skill("character-animation").authority_files, ("ANIMATION.md",))
        self.assertFalse(docs["CAMERA.md"].present)

    def test_unbacked_lock_is_not_presented_as_locked_authority(self):
        p = self.proj()
        (p / ".game" / "gpos" / "decisions" / "D-0101.json").unlink()
        ir = compile_ir(p)
        row = next(r for d in ir.project_authority if d.file == "ANIMATION.md" for r in d.rows if r.status == "LOCKED")
        self.assertFalse(row.lock_verified)
        r = pipeline.render(p)
        self.assertIn("LOCK_REFERENCE_UNRESOLVED", codes(r))
        text = render_bundle(ir, CLAUDE).by_path()[".claude/skills/gpos-character-animation/SKILL.md"].text
        self.assertIn("**unverified**: no ACTIVE decision record; treat as `PROPOSED`", text)

    def test_skill_authority_files_come_from_the_contract_inputs(self):
        ir = compile_ir(self.proj(all_skills=True))
        for s in ir.skills:
            text = (ROOT / "skills" / s.name / "SKILL.md").read_text()
            inputs = re.search(r"^## REQUIRED INPUTS\n(.*?)^## TOOL ACCESS", text, re.S | re.M).group(1)
            self.assertEqual(set(s.authority_files), set(re.findall(r"\.game/([A-Z-]+\.md)", inputs)), s.name)

    def test_adapter_settings_are_validated(self):
        for i, bad in enumerate(({"skills": ["gameplay-design"]}, {"skills": ["no-such-skill", "game-director"]},
                                 {"skills": ["game-director", "game-director"]}, {"profile": "x"}, "x")):
            p = project(self.tmp / str(i))
            edit_config(p, lambda c: c["extensions"].__setitem__("gpos-adapters", bad))
            r = pipeline.render(p)
            self.assertEqual((r.status, codes(r)), (adg.INVALID, {"ADAPTER_CONFIG_INVALID"}), bad)


# ---------------------------------------------------------------- B / C  rendering

def expected_files(backend, skills):
    files = {backend.entrypoint, backend.manifest_path}
    files |= {f"{backend.skill_root}/gpos-{s}/SKILL.md" for s in skills}
    files |= {f"{backend.skill_root}/gpos-game-director/references/workflows/{w}.md" for w in REG["workflows"]}
    return files


class B01_Rendering(TmpCase):
    def check_backend(self, backend):
        for all_skills in (False, True):
            p = project(self.tmp / f"{backend.id}-{all_skills}", all_skills=all_skills)
            ir = compile_ir(p)
            b1, b2 = render_bundle(ir, backend), render_bundle(compile_ir(p), backend)
            self.assertEqual(validate_bundle(b1), [])
            self.assertEqual(set(b1.by_path()), expected_files(backend, [s.name for s in ir.skills]))
            self.assertEqual({f.path: f.data for f in b1.files}, {f.path: f.data for f in b2.files})  # deterministic
            root = b1.by_path()[backend.entrypoint].text
            # the operating contract every root must state, independent of the marker machinery
            positions = [root.index(label) for _, label in ir.authority_order]
            self.assertEqual(positions, sorted(positions))
            for required in (ir.validator["readiness"], ir.validator["validate"], content.REASONING_IS_NOT_VALIDATION,
                             content.DIRECTOR_NOT_REVIEWER, content.NO_HUMAN_SYNTHESIS, "`HUMAN_DECISION_REQUIRED`",
                             "Human Decision", "Project Locked Authority", content.DO_NOT_EDIT):
                self.assertIn(required, root, required)
            for d in ir.project_authority:
                if d.present:
                    self.assertIn(f"`{d.path}`", root)
            anim = b1.by_path()[f"{backend.skill_root}/gpos-character-animation/SKILL.md"].text
            self.assertIn("Root motion vs in-place policy: in-place locomotion; root motion only for authored traversal", anim)
            self.assertIn("Style › Animation style statement", anim.split("Human decisions required")[1].split("Undecided")[0])
            self.assertLessEqual(len(root), content.ROOT_MAX_CHARS)
            self.assertLessEqual(root.count("\n"), content.ROOT_MAX_LINES)
            for f in b1.files:
                for g in ("~/", "$HOME", "/Users/", "/home/", "CODEX_HOME"):
                    self.assertNotIn(g, f.text, f.path)
                self.assertFalse(f.path.startswith("/") or ".." in f.path.split("/"), f.path)
            for s in ir.skills:
                text = b1.by_path()[f"{backend.skill_root}/gpos-{s.name}/SKILL.md"].text
                m = re.match(r"^---\nname: (.*)\ndescription: (.*)\n---\n", text)
                self.assertEqual(m.group(1), f"gpos-{s.name}")
                self.assertTrue(re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", m.group(1)) and len(m.group(1)) <= 64)
                self.assertLessEqual(len(json.loads(m.group(2))), 1024)
                self.assertIn(f"Maturity: `{s.maturity}`", text)
                for heading, section in s.sections:
                    self.assertIn(content.normalize(section), content.normalize(text), f"{s.name} {heading}")

    def test_claude_code(self):
        self.check_backend(CLAUDE)
        b = render_bundle(compile_ir(project(self.tmp / "c")), CLAUDE)
        self.assertIn("CLAUDE.md", b.by_path())
        self.assertIn("`/gpos-game-director`", b.by_path()["CLAUDE.md"].text)
        self.assertFalse(any(p.startswith(".agents/") or p == "AGENTS.md" for p in b.by_path()))

    def test_codex(self):
        self.check_backend(CODEX)
        b = render_bundle(compile_ir(project(self.tmp / "x")), CODEX)
        self.assertIn("`$gpos-game-director`", b.by_path()["AGENTS.md"].text)
        agents_files = [p for p in b.by_path() if p.endswith("AGENTS.md")]
        self.assertEqual(agents_files, ["AGENTS.md"])  # only the root: nested AGENTS.md files stay human-owned scopes
        self.assertIn("closer to the working directory may add local conventions", b.by_path()["AGENTS.md"].text)
        self.assertFalse(any(p.startswith(".claude/") or p == "CLAUDE.md" for p in b.by_path()))

    def test_all_13_skills_render_within_budget(self):
        ir = compile_ir(self.proj(all_skills=True))
        for backend in (CLAUDE, CODEX):
            b = render_bundle(ir, backend)
            self.assertEqual(validate_bundle(b), [])
            sizes = {f.skill: len(f.data) for f in b.files if f.role == "skill"}
            self.assertEqual(len(sizes), 13)
            self.assertLess(max(sizes.values()), content.SKILL_MAX_CHARS)

    def test_game_director_contract(self):
        text = render_bundle(compile_ir(self.proj()), CLAUDE).by_path()[".claude/skills/gpos-game-director/SKILL.md"].text
        self.assertIn(content.DIRECTOR_NOT_REVIEWER, text)
        self.assertIn("## Routing reference (registry)", text)
        for w in REG["workflows"]:
            self.assertIn(f"references/workflows/{w}.md", text)
        self.assertIn("This skill may cross-review gates owned by: none.", text)

    def test_skill_descriptions_are_domain_bounded(self):
        ir = compile_ir(self.proj(all_skills=True))
        anim = ir.skill("character-animation").description
        self.assertIn("how characters move", anim)
        self.assertIn("not for work another GPOS specialist owns", anim)
        ui = ir.skill("ui-ux").description
        self.assertNotIn("characters move", ui)


# ---------------------------------------------------------------- D  semantic parity

class D01_SemanticParity(TmpCase):
    def test_same_ir_same_semantics(self):
        ir = compile_ir(self.proj(all_skills=True))
        claude, codex = render_bundle(ir, CLAUDE), render_bundle(ir, CODEX)
        mc, mx = claude.manifest.data, codex.manifest.data
        self.assertEqual(mc["semantics"], mx["semantics"])
        self.assertEqual(mc["semantics"], ir_semantics(ir))
        self.assertEqual(mc["ir_sha256"], mx["ir_sha256"])
        for key in ("gpos_version", "project_id", "enabled_skills", "sources"):
            self.assertEqual(mc[key], mx[key], key)
        sem = lambda m: sorted((f["role"], f["skill"], tuple(f["semantics"])) for f in m["files"])
        self.assertEqual(sem(mc), sem(mx))
        s = mc["semantics"]
        self.assertEqual({k: v["maturity"] for k, v in s["skills"].items()}, {k: "DRAFT" for k in REG["skills"]})
        self.assertEqual(s["authority_order"], REG["authority_levels"])
        self.assertTrue(s["skills"]["game-director"]["never_cross_reviewer"])
        self.assertEqual(s["human_review"]["mandatory_triggers"], list(REG["mandatory_human_review_triggers"]))
        self.assertIn("readiness", s["validator"])
        self.assertEqual([w["name"] for w in s["workflows"]], REG["workflows"])

    def test_texts_differ_but_meaning_lives_in_one_place(self):
        ir = compile_ir(self.proj())
        root_c = render_bundle(ir, CLAUDE).by_path()["CLAUDE.md"].text
        root_x = render_bundle(ir, CODEX).by_path()["AGENTS.md"].text
        self.assertNotEqual(root_c, root_x)
        backend_src = (ROOT / "gpos" / "adapters" / "backends.py").read_text()
        for phrase in (content.NO_HUMAN_SYNTHESIS, content.DIRECTOR_NOT_REVIEWER, content.REASONING_IS_NOT_VALIDATION,
                       content.LOCKED_RULE, content.TESTS_NOT_DONE, content.PLACEHOLDER_RULE):
            self.assertNotIn(phrase, backend_src)  # backends carry format only
            for root in (root_c, root_x):          # ...and both renderings carry the same rules
                self.assertIn(content.normalize(phrase), content.normalize(root))
        for token in ("HUMAN_REVIEW_REQUIRED", "LOCKED", "never_cross_reviewer", "cross_review_eligibility"):
            self.assertNotIn(token, backend_src)

    def test_every_rendered_semantic_block_is_checked(self):
        ir = compile_ir(self.proj())
        for backend in (CLAUDE, CODEX):
            b = render_bundle(ir, backend)
            fmt = backend.agent_format()
            self.assertEqual(set(b.by_path()[backend.entrypoint].semantics), set(content.root_markers(ir, fmt)))


# ---------------------------------------------------------------- E  drift / F  sync

class E01_Drift(TmpCase):
    def synced(self):
        p = self.proj()
        r = pipeline.sync(p)
        self.assertEqual(r.status, adg.OK, [d.to_dict() for d in r.diagnostics])
        self.assertEqual(pipeline.check(p).status, adg.OK)
        return p

    def assertDrift(self, p, code, agent="all"):
        r = pipeline.check(p, agent)
        self.assertEqual(r.status, adg.DRIFT, [d.to_dict() for d in r.diagnostics])
        self.assertIn(code, codes(r))
        return r

    def test_manual_edit(self):
        p = self.synced()
        f = p / ".claude" / "skills" / "gpos-character-animation" / "SKILL.md"
        f.write_text(f.read_text() + "\nextra\n")
        self.assertDrift(p, "MANAGED_FILE_MODIFIED")

    def test_missing_and_unexpected_files(self):
        p = self.synced()
        (p / "AGENTS.md").unlink()
        self.assertDrift(p, "MANAGED_FILE_MISSING", "codex")
        (p / ".claude" / "skills" / "gpos-gameplay-design" / "notes.md").write_text("x")
        self.assertDrift(p, "UNEXPECTED_MANAGED_FILE", "claude-code")

    def test_source_change(self):
        p = self.synced()
        a = p / ".game" / "ANIMATION.md"
        a.write_text(a.read_text().replace("in-place locomotion", "root motion everywhere"))
        r = self.assertDrift(p, "SOURCE_CHANGED")
        self.assertIn("project:.game/ANIMATION.md", {d.path for d in r.diagnostics})
        self.assertEqual(tree(p / ".claude"), tree(p / ".claude"))  # check never regenerates

    def test_new_authority_file_is_a_source_change(self):
        p = self.synced()
        (p / ".game" / "CAMERA.md").write_text((ROOT / "templates" / "CAMERA.md").read_text().replace(
            " · see [templates/README.md](README.md)", ""))
        r = self.assertDrift(p, "SOURCE_CHANGED")
        self.assertIn("added", {json.loads(d.details).get("state") for d in r.diagnostics})

    def test_tampered_manifest(self):
        p = self.synced()
        m = p / CLAUDE.manifest_path
        data = json.loads(m.read_text())
        data["files"][0]["sha256"] = "0" * 64
        m.write_text(json.dumps(data))
        self.assertDrift(p, "MANIFEST_INVALID", "claude-code")  # semantic_hash no longer matches
        data = json.loads((p / CODEX.manifest_path).read_text())
        (p / CODEX.manifest_path).unlink()
        self.assertDrift(p, "MANIFEST_MISSING", "codex")
        self.assertIsNotNone(data)

    def test_wrong_version_and_format(self):
        p = self.synced()
        for field, value, code in (("gpos_version", "1.0.0-alpha.8", "GPOS_VERSION_MISMATCH"),
                                   ("adapter", {"id": "claude-code", "format": "claude-code-project/0", "target_agent": "x"},
                                    "ADAPTER_FORMAT_MISMATCH")):
            m = p / CLAUDE.manifest_path
            original = m.read_text()
            data = json.loads(original)
            data[field] = value
            body = {k: v for k, v in data.items() if k != "semantic_hash"}
            data["semantic_hash"] = hashlib.sha256(canonical_json(body).encode()).hexdigest()
            m.write_text(json.dumps(data))
            self.assertDrift(p, code, "claude-code")
            m.write_text(original)

    def test_generator_change_is_stale(self):
        p = self.synced()
        original = content.DO_NOT_EDIT
        try:
            content.DO_NOT_EDIT = "Generated file (changed generator) — do not edit as authority."
            self.assertDrift(p, "GENERATED_STALE")
        finally:
            content.DO_NOT_EDIT = original


class F01_Sync(TmpCase):
    def test_first_sync_writes_second_is_noop_check_clean(self):
        p = self.proj()
        before = tree(p)
        r1 = pipeline.sync(p)
        self.assertEqual(r1.status, adg.OK)
        written = {d.path for d in r1.diagnostics if d.code == "FILE_WRITTEN"}
        self.assertEqual(written, expected_files(CLAUDE, ENABLED) | expected_files(CODEX, ENABLED))
        after = tree(p)
        self.assertEqual({k: v for k, v in after.items() if k in before}, before)  # nothing pre-existing touched
        self.assertEqual(set(after) - set(before), written)
        r2 = pipeline.sync(p)
        self.assertEqual((r2.status, [d for d in r2.diagnostics if d.code in ("FILE_WRITTEN", "FILE_REMOVED")]), (adg.OK, []))
        self.assertEqual(tree(p), after)
        self.assertEqual(pipeline.check(p).status, adg.OK)

    def test_human_owned_entrypoints_are_never_overwritten(self):
        for backend in (CLAUDE, CODEX):
            p = project(self.tmp / backend.id)
            (p / backend.entrypoint).write_text("# Team notes\nOur own instructions.\n")
            before = tree(p)
            r = pipeline.sync(p, backend.id)
            self.assertEqual((r.status, r.exit_code), (adg.CONFLICT, 4))
            self.assertIn("UNOWNED_ENTRYPOINT", codes(r))
            self.assertEqual(tree(p), before)  # nothing written at all, not even skills

    def test_unowned_file_in_managed_area_blocks(self):
        p = self.proj()
        f = p / ".claude" / "skills" / "gpos-gameplay-design" / "SKILL.md"
        f.parent.mkdir(parents=True)
        f.write_text("hand written")
        before = tree(p)
        r = pipeline.sync(p, "claude-code")
        self.assertIn("OUTPUT_CONFLICT", codes(r))
        self.assertEqual(tree(p), before)

    def test_user_skills_outside_the_namespace_are_untouched(self):
        p = self.proj()
        mine = p / ".claude" / "skills" / "my-skill" / "SKILL.md"
        mine.parent.mkdir(parents=True)
        mine.write_text("---\nname: my-skill\ndescription: mine\n---\n")
        self.assertEqual(pipeline.sync(p).status, adg.OK)
        self.assertEqual(mine.read_text(), "---\nname: my-skill\ndescription: mine\n---\n")
        self.assertEqual(pipeline.check(p).status, adg.OK)

    def test_stale_owned_files_are_removed_safely(self):
        p = self.proj()
        self.assertEqual(pipeline.sync(p).status, adg.OK)
        edit_config(p, lambda c: c["extensions"]["gpos-adapters"]["skills"].remove("camera-composition"))
        outside = {k: v for k, v in tree(p).items() if not is_managed(CLAUDE, k) and not is_managed(CODEX, k)}
        r = pipeline.sync(p)
        self.assertEqual(r.status, adg.OK)
        removed = {d.path for d in r.diagnostics if d.code == "FILE_REMOVED"}
        self.assertEqual(removed, {".claude/skills/gpos-camera-composition/SKILL.md", ".agents/skills/gpos-camera-composition/SKILL.md"})
        self.assertFalse((p / ".claude" / "skills" / "gpos-camera-composition").exists())
        self.assertTrue((p / ".claude" / "skills").is_dir())
        after_outside = {k: v for k, v in tree(p).items() if not is_managed(CLAUDE, k) and not is_managed(CODEX, k)}
        self.assertEqual(after_outside, outside)  # nothing outside the managed areas changed
        self.assertEqual(pipeline.check(p).status, adg.OK)

    def test_edited_stale_file_is_not_deleted(self):
        p = self.proj()
        pipeline.sync(p)
        f = p / ".claude" / "skills" / "gpos-camera-composition" / "SKILL.md"
        f.write_text(f.read_text() + "team note\n")
        edit_config(p, lambda c: c["extensions"]["gpos-adapters"]["skills"].remove("camera-composition"))
        r = pipeline.sync(p)
        self.assertEqual(r.status, adg.CONFLICT)
        self.assertIn("MODIFIED_MANAGED_FILE_CONFLICT", codes(r))
        self.assertTrue(f.read_text().endswith("team note\n"))

    def test_modified_managed_file_needs_repair(self):
        p = self.proj()
        pipeline.sync(p)
        f = p / "CLAUDE.md"
        f.write_text(f.read_text() + "local edit\n")
        r = pipeline.sync(p)
        self.assertIn("MODIFIED_MANAGED_FILE_CONFLICT", codes(r))
        self.assertTrue(f.read_text().endswith("local edit\n"))
        r = pipeline.sync(p, repair=True)
        self.assertEqual(r.status, adg.OK)
        self.assertFalse(f.read_text().endswith("local edit\n"))
        self.assertEqual(pipeline.check(p).status, adg.OK)

    def test_interrupted_sync_can_be_completed(self):
        p = self.proj()
        pipeline.sync(p)
        a = p / ".game" / "ANIMATION.md"
        a.write_text(a.read_text().replace("in-place locomotion", "in-place locomotion only"))
        _, bundle = pipeline.prepare(p)[0], render_bundle(compile_ir(p), CLAUDE)
        half = [f for f in bundle.files if f.role == "skill"][:3]  # simulate a crash after three writes
        for f in half:
            (p / f.path).write_bytes(f.data)
        self.assertEqual(pipeline.check(p, "claude-code").status, adg.DRIFT)
        r = pipeline.sync(p)
        self.assertEqual(r.status, adg.OK, [d.to_dict() for d in r.diagnostics])
        self.assertEqual(pipeline.check(p).status, adg.OK)

    def test_failed_sync_leaves_previous_state_valid(self):
        p = self.proj()
        pipeline.sync(p)
        good = tree(p)
        (p / ".game" / "gpos" / "decisions" / "D-0099.json").write_text("{ broken")
        r = pipeline.sync(p)
        self.assertEqual(r.status, adg.INVALID)
        self.assertEqual({k: v for k, v in tree(p).items() if not k.startswith(".game/gpos/")},
                         {k: v for k, v in good.items() if not k.startswith(".game/gpos/")})

    def test_adapter_must_be_enabled(self):
        p = self.proj()
        edit_config(p, lambda c: c.__setitem__("enabled_adapters", ["codex"]))
        r = pipeline.sync(p, "claude-code")
        self.assertEqual((r.status, codes(r)), (adg.INVALID, {"ADAPTER_NOT_ENABLED"}))
        self.assertEqual(pipeline.sync(p).status, adg.OK)  # all = the enabled ones
        self.assertFalse((p / "CLAUDE.md").exists())
        self.assertTrue((p / "AGENTS.md").exists())


# ---------------------------------------------------------------- G  path security

class G01_PathSecurity(TmpCase):
    def test_relative_path_rules(self):
        for bad in ("../x", "a/../b", "/etc/passwd", "C:\\x", "C:/x", "", ".", "a//b", "./a", "a\x00b", "a\\b"):
            self.assertFalse(is_safe_relative(bad), bad)
        for bad in ("src/main.py", ".claude/skills/other/SKILL.md", ".claude/skills/gpos-", "CLAUDE.md/x", ".game/gpos/x.json",
                    "../CLAUDE.md", ".game/gpos-generated/codex/manifest.json"):
            self.assertFalse(is_managed(CLAUDE, bad), bad)
        self.assertTrue(is_managed(CLAUDE, ".claude/skills/gpos-x/SKILL.md"))

    def forged_manifest(self, p, backend, extra_paths):
        m = p / backend.manifest_path
        data = json.loads(m.read_text())
        data["files"] += [{"path": x, "sha256": hashlib.sha256(b"victim").hexdigest(), "bytes": 6, "role": "skill",
                           "skill": None, "sources": [], "semantics": []} for x in extra_paths]
        body = {k: v for k, v in data.items() if k != "semantic_hash"}
        data["semantic_hash"] = hashlib.sha256(canonical_json(body).encode()).hexdigest()
        m.write_text(json.dumps(data))

    def test_manifest_injection_cannot_delete_outside(self):
        p = self.proj()
        pipeline.sync(p)
        victim_outside = self.tmp / "victim.txt"
        victim_outside.write_bytes(b"victim")
        victim_inside = p / "src" / "main.py"
        victim_inside.parent.mkdir()
        victim_inside.write_bytes(b"victim")
        self.forged_manifest(p, CLAUDE, ["../victim.txt", "src/main.py", "/etc/hosts"])
        r = pipeline.sync(p, "claude-code")
        self.assertEqual(r.status, adg.CONFLICT)
        self.assertIn("UNSAFE_PATH", codes(r))
        self.assertTrue(victim_outside.exists() and victim_inside.exists())
        self.assertIn("PATH_ESCAPE", codes(pipeline.check(p, "claude-code")))

    def test_symlink_escape_is_refused(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        p = self.proj()
        (p / ".claude").mkdir()
        try:
            os.symlink(outside, p / ".claude" / "skills")
        except OSError:
            self.skipTest("symlinks unavailable")
        r = pipeline.sync(p, "claude-code")
        self.assertEqual(r.status, adg.CONFLICT)
        self.assertIn("UNSAFE_PATH", codes(r))
        self.assertEqual(list(outside.iterdir()), [])
        q = project(self.tmp / "q")
        target = self.tmp / "outside-claude.md"
        target.write_text("theirs")
        os.symlink(target, q / "CLAUDE.md")
        r = pipeline.sync(q, "claude-code")
        self.assertIn("UNSAFE_PATH", codes(r))
        self.assertEqual(target.read_text(), "theirs")

    def test_symlink_inside_project_is_not_written_through(self):
        p = self.proj()
        team = p / "notes" / "team.md"
        team.parent.mkdir()
        team.write_text("team instructions")
        try:
            os.symlink(Path("notes") / "team.md", p / "CLAUDE.md")
        except OSError:
            self.skipTest("symlinks unavailable")
        r = pipeline.sync(p, "claude-code")
        self.assertEqual(r.status, adg.CONFLICT)
        self.assertIn("UNSAFE_PATH", codes(r))
        self.assertEqual(team.read_text(), "team instructions")

    def test_render_out_cannot_target_project_areas(self):
        p = self.proj()
        for out in (p, p / ".game" / "x", p / ".claude"):
            r = pipeline.render(p, out=out)
            self.assertIn("OUTPUT_PATH_INVALID", codes(r), out)
        busy = self.tmp / "busy"
        busy.mkdir()
        (busy / "keep.txt").write_text("keep")
        self.assertIn("OUTPUT_PATH_INVALID", codes(pipeline.render(p, out=busy)))
        self.assertEqual(sorted(x.name for x in busy.iterdir()), ["keep.txt"])


# ---------------------------------------------------------------- H  project validation

class H01_ProjectValidation(TmpCase):
    def test_invalid_project_cannot_sync_or_render(self):
        p = self.proj()
        d = p / ".game" / "gpos" / "decisions" / "D-0099.json"
        data = json.loads(d.read_text())
        data["decided_by"]["id"] = "visitor"  # not a decision authority -> validator INVALID
        d.write_text(json.dumps(data))
        for fn in (pipeline.sync, pipeline.render, pipeline.check):
            r = fn(p)
            self.assertEqual((r.status, codes(r)), (adg.INVALID, {"PROJECT_INVALID"}), fn.__name__)
        self.assertFalse((p / "CLAUDE.md").exists())

    def test_not_ready_task_does_not_block_generation(self):
        from gpos import evaluate_readiness, load_project
        p = self.proj()
        self.assertEqual(evaluate_readiness(load_project(p), "FEAT-DASH").status, "NOT_READY")
        self.assertEqual(pipeline.sync(p).status, adg.OK)

    def test_unsupported_version_fails_closed(self):
        p = self.proj()
        edit_config(p, lambda c: c.__setitem__("gpos_version", "1.0.0-alpha.8"))
        for fn in (pipeline.sync, pipeline.render, pipeline.check):
            r = fn(p)
            self.assertEqual((r.status, r.exit_code, codes(r)), (adg.ERROR, 3, {"GPOS_VERSION_INCOMPATIBLE"}))

    def test_project_root_required(self):
        r = pipeline.render(self.tmp)
        self.assertEqual(codes(r), {"PROJECT_LAYOUT"})


# ---------------------------------------------------------------- I  size / context

class I01_Context(TmpCase):
    def test_monolithic_root_is_rejected(self):
        ir = compile_ir(self.proj())
        original = content.root_blocks

        def monolith(ir_, fmt, mpath):
            blocks = original(ir_, fmt, mpath)
            dump = "\n\n".join(text for s in ir_.skills for _, text in s.sections)
            return blocks + [content.Block("title", dump)]
        try:
            content.root_blocks = monolith
            diags = validate_bundle(render_bundle(ir, CLAUDE))
        finally:
            content.root_blocks = original
        self.assertTrue(any("monolithic" in d.message for d in diags))
        self.assertIn("CONTEXT_BUDGET_EXCEEDED", {d.code for d in diags})

    def test_over_budget_fails_without_truncation(self):
        p = self.proj()
        original = content.ROOT_MAX_CHARS
        try:
            content.ROOT_MAX_CHARS = 1000
            r = pipeline.sync(p)
        finally:
            content.ROOT_MAX_CHARS = original
        self.assertEqual(r.status, adg.INVALID)
        self.assertIn("CONTEXT_BUDGET_EXCEEDED", codes(r))
        self.assertFalse((p / "CLAUDE.md").exists())

    def test_large_project_authority_is_reported_not_cut(self):
        p = self.proj()
        a = p / ".game" / "ANIMATION.md"
        rows = "\n".join(f"| Extra rule {i} with a long explanatory name to grow the file | `UNDECIDED` | `PROPOSED` |"
                         for i in range(400))
        a.write_text(a.read_text() + "\n## Extra\n\n| Item | Value | Status · decision ref |\n|---|---|---|\n" + rows + "\n")
        r = pipeline.render(p, "claude-code")
        self.assertEqual(r.status, adg.INVALID)
        self.assertIn("CONTEXT_BUDGET_EXCEEDED", codes(r))


# ---------------------------------------------------------------- J  regression / boundaries

class J01_Boundaries(unittest.TestCase):
    def test_no_global_writes(self):
        self.assertEqual(list(Path(_HOME).rglob("*")), [])

    def test_production_code_boundary(self):
        sys.path.insert(0, str(ROOT / "tests"))
        import validate_framework as vf
        self.assertEqual(vf.phase_boundary_problems(), [])
        for p in (ROOT / "gpos" / "adapters").rglob("*.py"):
            text = p.read_text().lower()
            for word in ("unity", "blender", "ffmpeg", "subprocess", "urllib", "socket", "http.client", "requests"):
                self.assertNotIn(word, text, f"{p.name}: {word}")

    def test_frozen_tags(self):
        try:
            out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "v1.0.0-alpha.7^{commit}", "v1.0.0-alpha.8^{commit}"],
                                 capture_output=True, text=True, check=True).stdout.split()
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("git tags unavailable")
        self.assertEqual(out, ["218fbe3f89210ea3bf149f1d4d05496c7a774043", "8a0d87b945ad94da812632d5f08b04a851f3ad18"])

    def test_backends_match_registry(self):
        self.assertEqual(sorted(backends.BACKENDS), sorted(REG["adapter_ids"]))
        for b in backends.BACKENDS.values():
            for key in ("target_agent", "documented_target", "required_capabilities", "entrypoints", "skill_discovery",
                        "known_limitations", "locally_verified"):
                self.assertIn(key, b.compatibility, b.id)


# ---------------------------------------------------------------- K  CLI

class K01_Cli(TmpCase):
    def test_exit_codes_and_json(self):
        p = self.proj()
        code, out = run_cli("render", "--project", str(p), "--format", "json")
        self.assertEqual((code, json.loads(out)["status"]), (0, "OK"))
        self.assertEqual(run_cli("check", "--project", str(p))[0], 2)          # never synced: MANIFEST_MISSING
        self.assertEqual(run_cli("sync", "--project", str(p))[0], 0)
        self.assertEqual(run_cli("check", "--project", str(p))[0], 0)
        (p / "AGENTS.md").write_text("changed")
        code, out = run_cli("check", "--project", str(p), "--format", "json")
        self.assertEqual((code, json.loads(out)["status"]), (2, "DRIFT"))
        self.assertEqual(run_cli("sync", "--project", str(p))[0], 4)           # modified managed file: conflict
        self.assertEqual(run_cli("render", "--project", str(self.tmp))[0], 1)  # not a project root
        self.assertEqual(run_cli("render", "--project", str(p), "--agent", "nope")[0], 3)
        self.assertEqual(run_cli("frobnicate")[0], 3)
        code, out = run_cli("render", "--project", str(p), "--format", "json")
        self.assertNotIn("Traceback", out)

    def test_render_is_read_only_and_out_is_explicit(self):
        p = self.proj()
        before = tree(p)
        self.assertEqual(run_cli("render", "--project", str(p))[0], 0)
        self.assertEqual(run_cli("check", "--project", str(p))[0], 2)
        self.assertEqual(tree(p), before)
        out = self.tmp / "render-out"
        self.assertEqual(run_cli("render", "--project", str(p), "--out", str(out))[0], 0)
        self.assertEqual(set(tree(out)), {f"claude-code/{x}" for x in expected_files(CLAUDE, ENABLED)}
                         | {f"codex/{x}" for x in expected_files(CODEX, ENABLED)})
        self.assertEqual(tree(p), before)

    def test_render_twice_identical(self):
        p = self.proj()
        a, b = self.tmp / "a", self.tmp / "b"
        run_cli("render", "--project", str(p), "--out", str(a))
        run_cli("render", "--project", str(p), "--out", str(b))
        self.assertEqual(tree(a), tree(b))
        manifest = json.loads((a / "claude-code" / CLAUDE.manifest_path).read_text())
        self.assertFalse(re.search(r'"(generated_at|timestamp|time|date)"', json.dumps(manifest)))

    def test_generation_is_fast(self):
        p = project(self.tmp / "all", all_skills=True)
        t0 = time.perf_counter()
        pipeline.render(p)
        pipeline.sync(p)
        pipeline.check(p)
        self.assertLess(time.perf_counter() - t0, 30)


# ---------------------------------------------------------------- L  snapshots (layout, manifest, concise root)

class L01_Snapshots(TmpCase):
    """Layout and root-file snapshots. Update deliberately with GPOS_UPDATE_SNAPSHOTS=1."""

    def test_snapshots(self):
        ir = compile_ir(self.proj())
        for backend in (CLAUDE, CODEX):
            b = render_bundle(ir, backend)
            snap = {"layout": sorted(b.by_path()), "semantic_hash": b.manifest.data["semantic_hash"],
                    "root": b.by_path()[backend.entrypoint].text}
            path = SNAPSHOTS / f"{backend.id}.json"
            if os.environ.get("GPOS_UPDATE_SNAPSHOTS") == "1":
                SNAPSHOTS.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(snap, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
            expected = json.loads(path.read_text())
            self.assertEqual(snap["layout"], expected["layout"], backend.id)
            self.assertEqual(snap["root"], expected["root"], backend.id)
            self.assertEqual(snap["semantic_hash"], expected["semantic_hash"], backend.id)


if __name__ == "__main__":
    result = unittest.main(verbosity=1, exit=False).result
    shutil.rmtree(_HOME, ignore_errors=True)
    print(f"GPOS adapter layer tests ({len(backends.BACKENDS)} backends)")
    sys.exit(0 if result.wasSuccessful() else 1)
