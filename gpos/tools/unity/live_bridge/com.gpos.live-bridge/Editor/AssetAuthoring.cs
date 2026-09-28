// GPOS live bridge — the asset commands (bridge 1.2.0): asset-types, asset-find, asset-inspect (read only), and the
// four asset mutations create-material, set-material-property, create-scriptable-object and set-asset-property.
// There is no generic AssetDatabase call, no importer setting, no folder creation, no rename, no delete command, no
// SaveAssets and no global Refresh.
//
// Editing an existing asset (Persist), in this order:
//   resolve and verify identity (authorable: a main .mat or catalogued .asset alone in its file below Assets/)
//   refuse a dirty asset (unsaved Human edits are never saved by GPOS)       ASSET_DIRTY
//   refuse a file version control does not have open for edit                  ASSET_NOT_EDITABLE
//   targeted import of that one asset (ImportAsset) — from here the request reports mutation_started
//   refuse if the import left the asset dirty; recompute the composite token and compare     ASSET_CONFLICT
//   capture the source and .meta hashes, open one Undo group, make the typed change, read it back
//   immediately before saving, re-hash the source and .meta: any change reverts the group, nothing is saved
//   commit point: AssetDatabase.SaveAssetIfDirty(asset) — only that asset
//   post-commit verification; anything uncertain from the commit point on is PERSISTENCE_UNKNOWN (never retried)
// The edit stays in Unity's Undo history: Cmd-Z restores the value in memory and leaves the asset dirty while the
// file keeps the saved value until it is saved again.
//
// Creating an asset (Create) never overwrites anything and is not undoable. It is a transaction recorded in
// .game/gpos-runtime/unity/asset-create-txn/<project key>/<txn id>.json (CreateTxn) before anything is written:
//   the GPOS-owned scratch folder Assets/GposAssetTxn-<txn id> is created exclusively (mkdir fails if it exists)
//   CreateAsset writes the new asset inside it, named as the final file; its GUID, id, type and hashes are proven
//   the final path is re-checked (file, .meta, letter case, AssetDatabase) and ValidateMoveAsset must approve;
//   MoveAsset (which refuses an existing destination) moves it; the final identity and hashes are proven again
//   the empty scratch folder is removed after its identity is proven, then the record is cleared
// Before every creation, every earlier record is recovered from what is actually on disk (RecoverAll): nothing
// created -> cleared; an exact temporary asset with the final path still absent -> that scratch is removed; an exact
// final asset -> kept and the record closed; anything else -> CREATE_INCOMPLETE, nothing touched. No creation is
// replayed. A failure inside a request removes only an exact, proven temporary asset of its own transaction.
// Project code may run (AssetPostprocessors on import and move, ScriptableObject OnEnable and OnValidate).
// Bridge 1.3.0: the prefab creation (PrefabAuthoring.CreatePrefab) is a transaction of this same record store, with
// the same phases, steps, scratch folder, recovery and compensation; an undecidable PREFAB record is
// PREFAB_CREATE_INCOMPLETE. The test seam AfterStep covers the prefab commands' steps too.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using UnityEditor;
using UnityEngine;

namespace Gpos.LiveBridge
{
    internal static class AssetAuthoring
    {
        public const int TypesPage = 200;
        public const int ShadersPage = 10;

        [DllImport("libc", SetLastError = true)] static extern int mkdir(string path, int mode);
        [DllImport("libc", SetLastError = true)] static extern int access(string path, int mode);

        // TEST SEAM: called after each named step with the asset path. Only the test-only testkit sets it (to write
        // files at an exact point, or to stop the Editor process); a request cannot reach it.
        internal static Action<string, string> AfterStep;

        internal static void Step(string name, string path)
        {
            var hook = AfterStep;
            if (hook != null) hook(name, path);
        }

        static Request current;

        public static Dictionary<string, object> Run(Request r)
        {
            current = r;
            var a = r.Args;
            switch (r.Command)
            {
                case "asset-types": return Types(a);
                case "asset-find": return Find(a);
                case "asset-inspect": return Inspect(a);
                case "create-material": return CreateMaterial(a);
                case "set-material-property": return SetMaterialProperty(a);
                case "create-scriptable-object": return CreateScriptableObject(a);
                case "set-asset-property": return SetAssetProperty(a);
            }
            throw new Refusal("UNKNOWN_COMMAND", "the command is not in the closed allowlist");
        }

        static string Digest(Dictionary<string, object> a, string key)
        {
            string d = Authoring.Str(a, key, true);
            if (!ObjectIds.Digest.IsMatch(d)) throw new Refusal("BAD_ARGUMENTS", key + " must be 64 hex");
            return d;
        }

        static bool Matches(string text, string query) { return query == null || (text ?? "").IndexOf(query, StringComparison.OrdinalIgnoreCase) >= 0; }

        static Dictionary<string, object> Page<T>(List<T> all, int page, int size, string key, Func<T, object> view)
        {
            int start = page * size;
            return new Dictionary<string, object> {
                { "count", all.Count }, { "page", page }, { "page_size", size },
                { "next_page", start + size < all.Count ? (object)(page + 1) : null },
                { key, all.Skip(start).Take(size).Select(view).ToList() } };
        }

        // ------------------------------------------------------------ catalogs

        static Dictionary<string, object> Types(Dictionary<string, object> a)
        {
            string catalog = Authoring.Str(a, "catalog", true), query = Authoring.Str(a, "query");
            AssetBounds.CheckQuery(query);
            int page = Authoring.Int(a, "page", 0, 10000) ?? 0;
            switch (catalog)
            {
                case "KINDS":
                {
                    if (query != null || page != 0) throw new Refusal("BAD_ARGUMENTS", "the kind table takes no query and has one page");
                    return new Dictionary<string, object> {
                        { "catalog", catalog }, { "kinds_digest", AssetKinds.Digest() }, { "sources", AssetKinds.Sources.Cast<object>().ToList() },
                        { "kinds", AssetKinds.All.Select(k => (object)new Dictionary<string, object> {
                            { "kind", k }, { "findable", AssetKinds.Findable(k) }, { "names", AssetKinds.Table[k][1] },
                            { "authorable_extension", AssetKinds.Extension(k).Length == 0 ? null : AssetKinds.Extension(k) } }).ToList() } };
                }
                case "SCRIPTABLE_OBJECTS":
                {
                    var c = AssetCatalogs.ScriptableObjects();
                    var d = Page(c.Entries.Where(e => Matches(e.TypeId, query)).ToList(), page, TypesPage, "types", e => e.ToData());
                    d["catalog"] = catalog;
                    d["so_catalog_digest"] = c.Digest;
                    d["catalog_count"] = c.Entries.Count;
                    return d;
                }
                case "SHADERS":
                {
                    var c = AssetCatalogs.Shaders();
                    var d = Page(c.Entries.Where(e => Matches(e.Name, query)).ToList(), page, ShadersPage, "shaders", e => e.ToData(true));
                    d["catalog"] = catalog;
                    d["shader_catalog_digest"] = c.Digest;
                    d["catalog_count"] = c.Entries.Count;
                    return d;
                }
            }
            throw new Refusal("BAD_ARGUMENTS", "catalog is KINDS, SCRIPTABLE_OBJECTS or SHADERS");
        }

        // ------------------------------------------------------------ lookup (typed, bounded, paged; never a caller filter)

        static Dictionary<string, object> Find(Dictionary<string, object> a)
        {
            string kind = Authoring.Str(a, "kind", true), source = Authoring.Str(a, "source", true), query = Authoring.Str(a, "query");
            if (!AssetKinds.Findable(kind)) throw new Refusal("BAD_ARGUMENTS", "kind is one of the findable asset kinds");
            if (!AssetKinds.Sources.Contains(source)) throw new Refusal("BAD_ARGUMENTS", "source is ASSETS, PACKAGE or BUILTIN");
            AssetBounds.CheckQuery(query);
            int page = Authoring.Int(a, "page", 0, 10000) ?? 0;
            var found = new List<AssetRef>();
            int files = 0;
            var partial = new List<object>();
            if (source == AssetKinds.Builtin)
                found.AddRange(AssetResolver.BuiltinRefs(kind).Where(r => Matches(r.Object.name, query)));
            else
            {
                var folders = source == AssetKinds.Assets ? new List<string> { "Assets" } : AssetResolver.PackageRoots();
                var paths = folders.Count == 0 ? new List<string>() :
                    AssetDatabase.FindAssets(AssetKinds.Table[kind][0], folders.ToArray()).Select(AssetDatabase.GUIDToAssetPath)
                        .Where(p => !string.IsNullOrEmpty(p) && p.StartsWith(source == AssetKinds.Assets ? "Assets/" : "Packages/", StringComparison.Ordinal))
                        .Distinct().OrderBy(p => p, StringComparer.Ordinal).ToList();
                if (paths.Count > AssetBounds.MaxFindFiles)
                    throw new Refusal("ASSET_LIMIT", "more than " + AssetBounds.MaxFindFiles + " files hold this kind of asset in " + source + "; nothing is listed partially — narrow the lookup (another kind or source)");
                foreach (var path in paths)
                {
                    if (AssetResolver.EditorOnlyPath(path)) continue;
                    files++;
                    var objects = new List<UnityEngine.Object> { AssetDatabase.LoadMainAssetAtPath(path) };
                    objects.AddRange(AssetDatabase.LoadAllAssetRepresentationsAtPath(path));
                    if (objects.Count > AssetBounds.MaxObjectsPerFile && partial.Count < 20) partial.Add(path);
                    foreach (var o in objects.Take(AssetBounds.MaxObjectsPerFile))
                    {
                        if (o == null) continue;
                        string id = SceneObjects.Id(o);
                        if (!AssetIds.IsAsset(id)) continue;
                        AssetRef r;
                        try { r = AssetResolver.Classify(o, id); }
                        catch (Refusal) { continue; }
                        if (r.Kind == kind && Matches(o.name, query)) found.Add(r);
                    }
                }
            }
            found = found.GroupBy(r => r.Id).Select(g => g.First()).OrderBy(r => r.Path ?? "", StringComparer.Ordinal).ThenBy(r => r.Id, StringComparer.Ordinal).ToList();
            var d = Page(found, page, AssetBounds.FindPage, "assets", r => r.ToData());
            d["kind"] = kind;
            d["source"] = source;
            d["files_scanned"] = files;
            d["files_listed_partially"] = partial;
            return d;
        }

        // ------------------------------------------------------------ inspection (never imports, never writes)

        sealed class AssetState
        {
            public string Memory, File, Meta, Token;
            public bool Dirty;
        }

        static string MemoryHash(UnityEngine.Object o)
        {
            var b = new TokenBuilder("gpos.asset-memory/1");
            int n = SceneObjects.Serialized(b, o, "ASSET_LIMIT", "the asset");
            return b.Int(n).FinishFull();
        }

        static AssetState State(AssetRef r)
        {
            var s = new AssetState { Memory = MemoryHash(r.Object), Dirty = EditorUtility.IsDirty(r.Object),
                                     File = AssetFiles.FileSha(r.Path), Meta = AssetFiles.MetaSha(r.Path) };
            if (s.File == null || s.Meta == null) throw new Refusal("ASSET_REFUSED", "the asset's file or .meta file is missing on disk");
            s.Token = AssetToken.Of(r.Id, r.Path, r.Type, s.Memory, s.Dirty, s.File, s.Meta);
            return s;
        }

        static object IdOrNull(UnityEngine.Object o)
        {
            if (o == null) return null;
            return EditorUtility.IsPersistent(o) ? SceneObjects.Id(o) : null;
        }

        static object ShaderValue(Material m, ShaderProperty p)
        {
            switch (p.Kind)
            {
                case "color": { var c = m.GetColor(p.Name); return SceneObjects.Floats(c.r, c.g, c.b, c.a); }
                case "vector": { var v = m.GetVector(p.Name); return SceneObjects.Floats(v.x, v.y, v.z, v.w); }
                case "float":
                case "range": return (double)m.GetFloat(p.Name);
                case "int": return (long)m.GetInteger(p.Name);
                case "texture": return IdOrNull(m.GetTexture(p.Name));
            }
            return null;
        }

        static Dictionary<string, object> Inspect(Dictionary<string, object> a)
        {
            var r = AssetResolver.Resolve(Authoring.Str(a, "asset", true));
            string prefix = Authoring.Str(a, "path_prefix");
            if (prefix != null && prefix.Length > PropertyRules.MaxPathLength) throw new Refusal("BAD_ARGUMENTS", "path_prefix is too long");
            int page = Authoring.Int(a, "page", 0, 10000) ?? 0;
            var d = new Dictionary<string, object> { { "asset", r.ToData() } };
            switch (r.Kind)
            {
                case AssetKinds.Material:
                {
                    var m = (Material)r.Object;
                    var entry = AssetCatalogs.Shaders().Of(m.shader);
                    d["shader"] = new Dictionary<string, object> { { "id", IdOrNull(m.shader) }, { "name", m.shader == null ? null : m.shader.name }, { "in_catalog", entry != null } };
                    if (entry != null)
                        d["properties"] = entry.Properties.Select(p => { var x = p.ToData(); x["value"] = ShaderValue(m, p); return (object)x; }).ToList();
                    break;
                }
                case AssetKinds.Texture:
                {
                    var t = (Texture)r.Object;
                    d["texture"] = new Dictionary<string, object> { { "dimension", t.dimension.ToString() }, { "width", t.width }, { "height", t.height } };
                    break;
                }
                case AssetKinds.Sprite:
                {
                    var s = (Sprite)r.Object;
                    d["sprite"] = new Dictionary<string, object> { { "texture", IdOrNull(s.texture) } };   // a Material takes the Texture's own id
                    break;
                }
                case AssetKinds.Prefab:
                case AssetKinds.Model:
                {
                    var components = ((GameObject)r.Object).GetComponents<Component>();
                    d["root_components"] = components.Take(AssetBounds.MaxRootComponents).Where(c => c != null)
                        .Select(c => (object)new Dictionary<string, object> { { "id", SceneObjects.Id(c) }, { "type", Catalog.TypeKey(c.GetType()) } }).ToList();
                    d["root_components_truncated"] = components.Length > AssetBounds.MaxRootComponents;
                    break;
                }
                case AssetKinds.PrefabComponent:
                    d["root"] = SceneObjects.Id(((Component)r.Object).gameObject);
                    break;
                case AssetKinds.ScriptableObject:
                {
                    var entry = AssetCatalogs.ScriptableObjects().Find(r.Type);
                    d["type"] = entry.ToData();
                    var all = Properties.Entries(r.Object, prefix);
                    var listing = Page(all, page, Properties.PageSize, "properties", x => x);
                    foreach (var kv in listing) d[kv.Key] = kv.Value;
                    break;
                }
            }
            if (AssetResolver.Authorable(r))
            {
                try
                {
                    var s = State(r);
                    d["tokens"] = Authoring.Tokens("asset", s.Token);
                    d["dirty"] = s.Dirty;
                    d["file_sha256"] = s.File;
                    d["meta_sha256"] = s.Meta;
                    d["editable"] = AssetDatabase.IsOpenForEdit(r.Path, StatusQueryOptions.UseCachedIfPossible);
                }
                catch (Refusal problem)
                {
                    d["tokens"] = Authoring.Tokens("asset", null);
                    d["token_problem"] = problem.Code + ": " + problem.Message;
                }
            }
            return d;
        }

        // ------------------------------------------------------------ editing an existing asset

        static Dictionary<string, object> Started(params object[] pairs)
        {
            var d = new Dictionary<string, object> { { "mutation_started", true }, { "import_performed", true }, { "value_persisted", false } };
            for (int i = 0; i < pairs.Length; i += 2) d[(string)pairs[i]] = pairs[i + 1];
            return d;
        }

        static Refusal After(string code, string message, Dictionary<string, object> data, string status = "REFUSED")
        {
            return new Refusal(code, message) { Status = status, Data = data };
        }

        // Reverts the asset's GPOS Undo group and checks the in-memory state is the one before the edit.
        static Refusal Reverted(int group, string id, string memoryBefore, string code, string message)
        {
            string problem = null;
            try { Undo.RevertAllDownToGroup(group); }
            catch (Exception e) { problem = e.GetType().Name; }
            bool restored = false;
            bool dirty = false;
            try
            {
                var now = AssetResolver.Resolve(id);
                restored = problem == null && MemoryHash(now.Object) == memoryBefore;
                dirty = EditorUtility.IsDirty(now.Object);
            }
            catch (Exception) { }
            var data = Started("reverted", problem == null, "restored", restored, "dirty", dirty);
            if (!restored)
                return After("ROLLBACK_INCOMPLETE", message + "; the revert did not restore the asset's in-memory state — nothing was saved; re-inspect before anything else", data, "FAILED");
            return After(code, message + "; the edit was reverted in memory and nothing was saved" + (dirty ? " (Unity still marks the asset dirty)" : ""), data,
                         code == "AUTHORING_FAILED" ? "FAILED" : "REFUSED");
        }

        static Dictionary<string, object> Persist(AssetRef r, string expected, string undoName, Action<UnityEngine.Object> change,
                                                  Func<UnityEngine.Object, bool> holds, Func<UnityEngine.Object, Dictionary<string, object>> report)
        {
            string path = r.Path, id = r.Id;
            if (EditorUtility.IsDirty(r.Object))
                throw new Refusal("ASSET_DIRTY", "the asset has unsaved changes in the Editor; GPOS never saves them — save or revert it in the Editor, re-inspect and decide again (nothing was changed)");
            if (!AssetDatabase.IsOpenForEdit(path, StatusQueryOptions.UseCachedIfPossible))
                throw new Refusal("ASSET_NOT_EDITABLE", "version control does not have the asset open for edit; GPOS never checks files out (nothing was changed)");
            AssetFiles.LinkFree(path);
            if (AssetFiles.FileSha(path) == null) throw new Refusal("ASSET_REFUSED", "the asset's file is missing on disk");
            AssetFiles.MetaSha(path);
            // ---- the targeted import: from here on the request reports that it changed Unity's state
            AssetDatabase.ImportAsset(path);
            Step("import", path);
            AssetState before;
            try
            {
                r = AssetResolver.Resolve(id);
                if (EditorUtility.IsDirty(r.Object)) throw After("ASSET_DIRTY", "the asset is dirty after its import; nothing was written", Started());
                before = State(r);
            }
            catch (Refusal refusal)
            {
                if (refusal.Data == null) refusal.Data = Started();
                throw;
            }
            if (before.Token != expected)
                throw After("ASSET_CONFLICT", "the asset changed since it was inspected (in memory, on disk or in its .meta); re-inspect and decide again — nothing was written",
                            Started("tokens_now", Authoring.Tokens("asset", before.Token)));
            var o = r.Object;
            Undo.IncrementCurrentGroup();
            int group = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName(undoName);
            try
            {
                change(o);
                EditorUtility.SetDirty(o);
                if (!holds(o)) throw new Refusal("VALUE_NOT_APPLIED", "Unity or project code stored a different value than the one written");
            }
            catch (Refusal refusal) { throw Reverted(group, id, before.Memory, refusal.Code, refusal.Message); }
            catch (Exception e) { throw Reverted(group, id, before.Memory, "AUTHORING_FAILED", "Unity raised " + e.GetType().Name + " during the edit"); }
            Undo.CollapseUndoOperations(group);
            Undo.IncrementCurrentGroup();
            // ---- immediately before saving: the file and its .meta must still be exactly what was imported
            Step("pre-save", path);
            string fileNow, metaNow;
            try { fileNow = AssetFiles.FileSha(path); metaNow = AssetFiles.MetaSha(path); }
            catch (Refusal) { fileNow = metaNow = null; }
            if (fileNow != before.File || metaNow != before.Meta)
                throw Reverted(group, id, before.Memory, "ASSET_CONFLICT", "the asset's file or .meta changed on disk before it was saved");
            // ---- the commit point: only this asset is saved
            try { AssetDatabase.SaveAssetIfDirty(o); }
            catch (Exception e)
            {
                throw After("PERSISTENCE_UNKNOWN", "Unity raised " + e.GetType().Name + " while saving the asset; whether it was written is unknown — it is not retried, re-inspect",
                            new Dictionary<string, object> { { "mutation_started", true }, { "import_performed", true }, { "commit_started", true } }, "FAILED");
            }
            Step("saved", path);
            Dictionary<string, object> result;
            try
            {
                var after = AssetResolver.Resolve(id);
                var s = State(after);
                if (s.Dirty || !holds(after.Object))
                    throw new Refusal("PERSISTENCE_UNKNOWN", "after the save the asset is still dirty or holds another value");
                result = report(after.Object);
                result["asset"] = after.ToData();
                result["tokens"] = Authoring.Tokens("asset", s.Token);
                result["file_sha256"] = s.File;
                result["meta_sha256"] = s.Meta;
                result["file_changed"] = s.File != before.File;
            }
            catch (Exception e)
            {
                throw After("PERSISTENCE_UNKNOWN", "the save could not be verified (" + (e is Refusal ? e.Message : e.GetType().Name) + "); it is not retried, re-inspect",
                            new Dictionary<string, object> { { "mutation_started", true }, { "import_performed", true }, { "commit_started", true } }, "FAILED");
            }
            result["undo_group"] = undoName;
            result["import_performed"] = true;
            result["value_persisted"] = true;
            return result;
        }

        static Dictionary<string, object> SetMaterialProperty(Dictionary<string, object> a)
        {
            var r = AssetResolver.RequireAuthorable(AssetResolver.Resolve(Authoring.Str(a, "material", true)), AssetKinds.Material);
            string name = Authoring.Str(a, "property", true), kind = Authoring.Str(a, "kind", true);
            var m = (Material)r.Object;
            var entry = AssetCatalogs.Shaders().Of(m.shader);
            if (entry == null) throw new Refusal("SHADER_NOT_IN_CATALOG", "the material's shader is not in the shader catalog; its properties are never written");
            var p = entry.Properties.FirstOrDefault(x => x.Name == name);
            if (p == null || m.shader.FindPropertyIndex(name) < 0)
                throw new Refusal("PROPERTY_UNSUPPORTED", "the material's shader does not declare this property (Unity would accept any name silently)");
            if (p.Refusal != null) throw new Refusal("PROPERTY_UNSUPPORTED", "the shader property cannot be written (" + p.Refusal + ")");
            if (p.Kind != kind) throw new Refusal("PROPERTY_UNSUPPORTED", "the shader property is of kind " + p.Kind + ", not " + kind);
            var value = MaterialRules.Parse(kind, a["value"], p.RangeMin, p.RangeMax);
            Texture texture = null;
            if (kind == "texture" && value.TextureId != null)
            {
                var t = AssetResolver.Resolve(value.TextureId);
                if (t.Kind == AssetKinds.Sprite)
                    throw new Refusal("VALUE_INVALID", "a Sprite is not a Texture: a material takes the Texture asset's own id (asset-inspect of the Sprite names it)");
                if (t.Kind != AssetKinds.Texture) throw new Refusal("VALUE_INVALID", "a shader texture property takes a TEXTURE asset, not a " + t.Kind);
                texture = (Texture)t.Object;
                if (!MaterialRules.DimensionMatches(p.Dimension, texture.dimension.ToString()))
                    throw new Refusal("VALUE_INVALID", "the texture is " + texture.dimension + "; the shader property takes " + p.Dimension);
            }
            string expected = Authoring.Token(a, "expected_asset_token", true);
            return Persist(r, expected, "GPOS: set " + name + " of " + m.name, o =>
            {
                var mat = (Material)o;
                Undo.RecordObject(mat, "GPOS: set " + name + " of " + mat.name);
                switch (kind)
                {
                    case "color": mat.SetColor(name, new Color(value.Floats[0], value.Floats[1], value.Floats[2], value.Floats[3])); break;
                    case "vector": mat.SetVector(name, new Vector4(value.Floats[0], value.Floats[1], value.Floats[2], value.Floats[3])); break;
                    case "float":
                    case "range": mat.SetFloat(name, value.Float); break;
                    case "int": mat.SetInteger(name, value.Int); break;
                    case "texture": mat.SetTexture(name, texture); break;
                }
            }, o => MaterialHolds((Material)o, name, value, texture),
               o => new Dictionary<string, object> { { "property", new Dictionary<string, object> { { "name", name }, { "kind", kind }, { "value", ShaderValue((Material)o, p) } } } });
        }

        static bool MaterialHolds(Material m, string name, MaterialValue v, Texture texture)
        {
            var f = v.Floats;
            switch (v.Kind)
            {
                case "color": { var c = m.GetColor(name); return c.r.Equals(f[0]) && c.g.Equals(f[1]) && c.b.Equals(f[2]) && c.a.Equals(f[3]); }
                case "vector": { var x = m.GetVector(name); return x.x.Equals(f[0]) && x.y.Equals(f[1]) && x.z.Equals(f[2]) && x.w.Equals(f[3]); }
                case "float":
                case "range": return m.GetFloat(name).Equals(v.Float);
                case "int": return m.GetInteger(name) == v.Int;
                case "texture": return m.GetTexture(name) == texture;
            }
            return false;
        }

        static Dictionary<string, object> SetAssetProperty(Dictionary<string, object> a)
        {
            var r = AssetResolver.RequireAuthorable(AssetResolver.Resolve(Authoring.Str(a, "asset", true)), AssetKinds.ScriptableObject);
            string path = Authoring.Str(a, "path", true), kind = Authoring.Str(a, "kind", true);
            var so = new SerializedObject(r.Object);
            var p = Properties.Writable(r.Object, so, path, kind);
            var value = PropertyRules.Parse(kind, a["value"]);
            int enumIndex = -1;
            UnityEngine.Object target = null;
            if (kind == "enum")
            {
                var names = p.enumNames;
                var matches = Enumerable.Range(0, names.Length).Where(i => names[i] == value.String).ToList();
                if (matches.Count != 1) throw new Refusal("VALUE_INVALID", "the value is not exactly one of the enum's names");
                enumIndex = matches[0];
            }
            if (kind == "object" && value.String != null)
            {
                if (!AssetIds.IsAsset(value.String)) throw new Refusal("VALUE_INVALID", "an asset references assets only, never Scene objects");
                target = AssetResolver.Resolve(value.String).Object;
                if (!Properties.Accepts(PropertyRules.PPtrType(p.type), target))
                    throw new Refusal("VALUE_INVALID", "the field does not accept that asset's type");
            }
            string expected = Authoring.Token(a, "expected_asset_token", true);
            return Persist(r, expected, "GPOS: set " + path + " of " + r.Object.name, o =>
            {
                var s = new SerializedObject(o);
                Properties.Assign(s.FindProperty(path), value, enumIndex, target);
                s.ApplyModifiedProperties();
            }, o =>
            {
                var q = new SerializedObject(o).FindProperty(path);
                return q != null && Properties.Holds(q, value, enumIndex, target);
            }, o => new Dictionary<string, object> { { "property", new Dictionary<string, object> {
                    { "path", path }, { "kind", kind }, { "value", Properties.Read(new SerializedObject(o).FindProperty(path), kind, o) } } } });
        }

        // ------------------------------------------------------------ creation

        static Dictionary<string, object> CreateMaterial(Dictionary<string, object> a)
        {
            string path = Authoring.Str(a, "path", true), shaderId = Authoring.Str(a, "shader", true);
            AssetPaths.CheckWritePath(path, AssetKinds.Extension(AssetKinds.Material));
            string digest = Digest(a, "expected_shader_catalog_digest");
            AssetIds.Check(shaderId);
            var catalog = AssetCatalogs.Shaders();
            var entry = catalog.Find(shaderId);
            if (entry == null) throw new Refusal("SHADER_NOT_IN_CATALOG", "the shader is not in the shader catalog");
            if (catalog.Digest != digest) throw new Refusal("CATALOG_CHANGED", "the shader catalog changed since it was read; read it again (nothing was changed)");
            var shader = catalog.Shaders[shaderId];
            var d = Create(AssetKinds.Material, path, () => new Material(shader), o => o is Material && ((Material)o).shader == shader);
            d["shader"] = entry.ToData(false);
            return d;
        }

        static Dictionary<string, object> CreateScriptableObject(Dictionary<string, object> a)
        {
            string path = Authoring.Str(a, "path", true), typeId = Authoring.Str(a, "type_id", true);
            AssetPaths.CheckWritePath(path, AssetKinds.Extension(AssetKinds.ScriptableObject));
            if (!ObjectIds.TypeId.IsMatch(typeId)) throw new Refusal("TYPE_NOT_IN_CATALOG", "a ScriptableObject type is named <assembly>::<full name>");
            string digest = Digest(a, "expected_so_catalog_digest");
            var catalog = AssetCatalogs.ScriptableObjects();
            var entry = catalog.Find(typeId);
            if (entry == null) throw new Refusal("TYPE_NOT_IN_CATALOG", "the type is not in the ScriptableObject catalog");
            if (!entry.Creatable) throw new Refusal("TYPE_NOT_IN_CATALOG", "the type is referenced and edited but not created: it has no [CreateAssetMenu]");
            if (catalog.Digest != digest) throw new Refusal("CATALOG_CHANGED", "the ScriptableObject catalog changed since it was read; read it again (nothing was changed)");
            var type = catalog.Types[typeId];
            var d = Create(AssetKinds.ScriptableObject, path, () => ScriptableObject.CreateInstance(type), o => o != null && o.GetType() == type);
            d["type"] = entry.ToData();
            return d;
        }

        internal static void CheckFolder(string folder)
        {
            AssetFiles.LinkFree(folder);
            string full = AssetFiles.Full(folder);
            if (!Directory.Exists(full) || !AssetDatabase.IsValidFolder(folder))
                throw new Refusal("ASSET_PATH_INVALID", "the folder " + folder + " does not exist; GPOS never creates folders");
            if (access(full, 2) != 0) throw new Refusal("ASSET_NOT_EDITABLE", "the folder " + folder + " is not writable");
        }

        // Refuses (ASSET_EXISTS) when anything is at the final path: a file, a folder, a link, an orphan .meta, the same
        // name in another letter case, or an asset the AssetDatabase still knows there.
        internal static void CheckAbsent(string folder, string path)
        {
            string file = path.Substring(folder.Length + 1);
            if (AssetFiles.Present(path) || AssetFiles.Present(path + ".meta"))
                throw new Refusal("ASSET_EXISTS", "something already exists at " + path + " (a file or an orphan .meta); GPOS never overwrites and never picks another name");
            if (AssetFiles.CaseCollision(folder, file) || AssetFiles.CaseCollision(folder, file + ".meta"))
                throw new Refusal("ASSET_EXISTS", "a file whose name differs only in letter case exists in " + folder);
            if (!string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(path, AssetPathToGUIDOptions.OnlyExistingAssets)))
                throw new Refusal("ASSET_EXISTS", "the AssetDatabase knows an asset at " + path);
        }

        static Dictionary<string, object> Create(string kind, string path, Func<UnityEngine.Object> make, Func<UnityEngine.Object, bool> typeCheck)
        {
            string ext = AssetKinds.Extension(kind);
            string folder = AssetPaths.CheckWritePath(path, ext), stem = AssetPaths.StemOf(path, ext);
            if (UnityEditor.VersionControl.Provider.isActive)
                throw new Refusal("ASSET_NOT_EDITABLE", "a version-control provider is active; GPOS never adds or checks out files, so it creates no asset here");
            CheckFolder(folder);
            CheckAbsent(folder, path);
            var recovered = CreateTxns.RecoverAll();
            bool changed = recovered.Any(x => (bool)((Dictionary<string, object>)x)["changed"]);
            var txn = new CreateTxn {
                TxnId = Guid.NewGuid().ToString("N"), ProjectKey = LiveBridge.Place.Key, SessionId = current.SessionId, RequestId = current.Id,
                Owner = current.Owner, Kind = kind, FinalPath = path, Phase = CreateTxn.Prepared,
                StartedUtc = DateTime.UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss.fffffff'Z'", CultureInfo.InvariantCulture) };
            try
            {
                CheckFolder(folder);
                CheckAbsent(folder, path);
                CreateTxns.Write(txn);
            }
            catch (Refusal refusal)
            {
                refusal.Data = new Dictionary<string, object> { { "mutation_started", changed }, { "recovered", recovered } };
                throw;
            }
            Step("prepared", path);
            var state = new CreateState { Txn = txn, Started = changed };
            try
            {
                return Transact(state, folder, path, stem, ext, make, typeCheck, recovered);
            }
            catch (Refusal refusal)
            {
                if (refusal.Code == "CREATE_INCOMPLETE" && refusal.Data != null) throw;   // already decided: the record is kept
                throw Compensate(state, refusal.Code, refusal.Message, refusal.Code == "AUTHORING_FAILED" ? "FAILED" : "REFUSED", recovered);
            }
            catch (Exception e)
            {
                throw Compensate(state, "AUTHORING_FAILED", "Unity raised " + e.GetType().Name + " while creating the asset", "FAILED", recovered);
            }
        }

        internal sealed class CreateState
        {
            public CreateTxn Txn;
            public bool Started, MoveAttempted;
            public UnityEngine.Object Created;
        }

        static Dictionary<string, object> Transact(CreateState st, string folder, string path, string stem, string ext, Func<UnityEngine.Object> make,
                                                   Func<UnityEngine.Object, bool> typeCheck, List<object> recovered)
        {
            var txn = st.Txn;
            string scratch = txn.ScratchFolder, temp = txn.TempPath, file = stem + ext;
            if (AssetFiles.Present(scratch) || AssetFiles.Present(scratch + ".meta"))
                throw new Refusal("CREATE_INCOMPLETE", "the transaction's scratch folder already exists; nothing was written");
            // ---- the GPOS-owned scratch namespace: created exclusively, so nothing of anyone else can be inside it
            st.Started = true;
            if (mkdir(AssetFiles.Full(scratch), Convert.ToInt32("755", 8)) != 0)
                throw new Refusal("CREATE_INCOMPLETE", "the scratch folder could not be created exclusively (errno " + Marshal.GetLastWin32Error() + ")");
            AssetDatabase.ImportAsset(scratch);
            var listing = AssetFiles.Listing(scratch);
            txn.ScratchMeta = AssetFiles.MetaSha(scratch);
            if (!AssetDatabase.IsValidFolder(scratch) || listing == null || listing.Count != 0 || txn.ScratchMeta == null)
                throw new Refusal("AUTHORING_FAILED", "Unity did not register the scratch folder");
            txn.Phase = CreateTxn.ScratchReady;
            CreateTxns.Write(txn);
            Step("scratch", path);
            listing = AssetFiles.Listing(scratch);
            if (listing == null || listing.Count != 0) throw new Refusal("AUTHORING_FAILED", "unknown content appeared in the scratch folder before the asset was created");
            // ---- the new asset, created in the scratch folder under its final file name
            st.Created = make();
            AssetDatabase.CreateAsset(st.Created, temp);
            Step("temp-created", path);
            listing = AssetFiles.Listing(scratch);
            string guid = AssetDatabase.AssetPathToGUID(temp, AssetPathToGUIDOptions.OnlyExistingAssets);
            var loaded = AssetDatabase.LoadMainAssetAtPath(temp);
            string localId = txn.Kind == AssetKinds.Material ? "2100000" : "11400000";
            if (listing == null || !listing.SequenceEqual(new[] { file, file + ".meta" }) || string.IsNullOrEmpty(guid) || loaded == null ||
                loaded != st.Created || !typeCheck(loaded) || loaded.name != stem || AssetFiles.MetaGuid(temp) != guid ||
                SceneObjects.Id(loaded) != "GlobalObjectId_V1-3-" + guid + "-" + localId + "-0" || EditorUtility.IsDirty(loaded))
                throw new Refusal("AUTHORING_FAILED", "the created asset is not exactly the one GPOS asked Unity to create");
            txn.Guid = guid;
            txn.GlobalId = SceneObjects.Id(loaded);
            txn.Type = Catalog.TypeKey(loaded.GetType());
            txn.TempFile = AssetFiles.FileSha(temp);
            txn.TempMeta = AssetFiles.MetaSha(temp);
            txn.Phase = CreateTxn.TempProven;
            CreateTxns.Write(txn);
            Step("temp-proven", path);
            // ---- the final destination, checked again immediately before the move; MoveAsset never overwrites
            Step("pre-move", path);
            CheckFolder(folder);
            CheckAbsent(folder, path);
            string valid = AssetDatabase.ValidateMoveAsset(temp, path);
            if (!string.IsNullOrEmpty(valid)) throw new Refusal("ASSET_EXISTS", "Unity refuses the destination: " + valid);
            st.MoveAttempted = true;
            string error = AssetDatabase.MoveAsset(temp, path);
            Step("moved", path);
            if (!string.IsNullOrEmpty(error)) throw new Refusal("AUTHORING_FAILED", "Unity did not move the asset into place: " + error);
            string finalFile = AssetFiles.FileSha(path), finalMeta = AssetFiles.MetaSha(path);
            var placed = AssetDatabase.LoadMainAssetAtPath(path);
            if (placed != st.Created || AssetDatabase.AssetPathToGUID(path, AssetPathToGUIDOptions.OnlyExistingAssets) != guid ||
                SceneObjects.Id(placed) != txn.GlobalId || Catalog.TypeKey(placed.GetType()) != txn.Type || placed.name != stem ||
                AssetFiles.MetaGuid(path) != guid || finalFile != txn.TempFile || finalMeta != txn.TempMeta || EditorUtility.IsDirty(placed) ||
                AssetFiles.Present(temp) || AssetFiles.Present(temp + ".meta"))
                throw new Refusal("AUTHORING_FAILED", "the moved asset is not exactly the one GPOS created");
            txn.FinalFile = finalFile;
            txn.FinalMeta = finalMeta;
            txn.Phase = CreateTxn.FinalProven;
            CreateTxns.Write(txn);
            Step("final-proven", path);
            var created = AssetResolver.Classify(placed, txn.GlobalId).ToData();
            // ---- the scratch folder is removed only when it is exactly the empty folder GPOS created
            string scratchProblem = CreateTxns.RemoveScratch(txn);
            if (scratchProblem != null)
                throw new Refusal("CREATE_INCOMPLETE", "the asset was created at " + path + ", but the transaction's scratch folder " + scratch + " " + scratchProblem +
                                                       "; it is left untouched and the transaction stays open — remove the unknown content, then any later creation closes it")
                      { Data = new Dictionary<string, object> { { "mutation_started", true }, { "created", created }, { "txn_id", txn.TxnId }, { "recovered", recovered } } };
            Step("scratch-removed", path);
            CreateTxns.Delete(txn);
            Step("cleared", path);
            var token = State(AssetResolver.Resolve(txn.GlobalId));
            return new Dictionary<string, object> {
                { "created", created }, { "txn_id", txn.TxnId }, { "tokens", Authoring.Tokens("asset", token.Token) },
                { "file_sha256", finalFile }, { "meta_sha256", finalMeta }, { "undoable", false }, { "recovered", recovered } };
        }

        // A creation that stopped inside this request: removes only its own exact, proven temporary asset (never the
        // final path) and its exact empty scratch folder, then clears the record; anything else keeps the record and
        // is ROLLBACK_INCOMPLETE.
        internal static Refusal Compensate(CreateState st, string code, string message, string status, List<object> recovered)
        {
            var txn = st.Txn;
            if (!st.Started)
            {
                CreateTxns.Delete(txn);
                return new Refusal(code, message + "; nothing was written") { Status = status, Data = new Dictionary<string, object> {
                    { "mutation_started", recovered.Any(x => (bool)((Dictionary<string, object>)x)["changed"]) }, { "recovered", recovered } } };
            }
            var facts = CreateTxns.Facts(txn);
            string outcome = null;
            if (!st.MoveAttempted)
            {
                try { outcome = CreateTxns.RemoveOwnTemp(txn, facts); }
                catch (Exception) { outcome = null; }
            }
            var data = new Dictionary<string, object> { { "mutation_started", true }, { "txn_id", txn.TxnId }, { "state", facts.ToData() }, { "recovered", recovered } };
            if (outcome == null)
                return new Refusal("ROLLBACK_INCOMPLETE", message + "; the creation could not be undone from proven facts — nothing unknown was removed, the transaction stays open; inspect the paths it names")
                       { Status = "FAILED", Data = data };
            data["compensated"] = outcome;
            return new Refusal(code, message + "; GPOS removed only its own proven temporary asset and scratch folder (" + outcome + ")") { Status = status, Data = data };
        }
    }

    // The asset-creation transaction records of this project and the recovery of interrupted creations.
    internal static class CreateTxns
    {
        static readonly Regex RecordName = new Regex("^([0-9a-f]{32})\\.json\\z");

        static Refusal Incomplete(string why, Dictionary<string, object> data = null, string code = "CREATE_INCOMPLETE")
        {
            return new Refusal(code, why) { Data = data ?? new Dictionary<string, object> { { "mutation_started", false } } };
        }

        // .game/gpos-runtime/unity/asset-create-txn/<project key>, every component a real directory (never a link).
        static string Dir(bool create)
        {
            var place = LiveBridge.Place;
            string runtime = Path.Combine(place.GposRoot, ".game", "gpos-runtime");
            string[] chain = { runtime, Path.Combine(runtime, "unity"), Path.Combine(runtime, "unity", "asset-create-txn"),
                               Path.Combine(runtime, "unity", "asset-create-txn", place.Key) };
            foreach (var d in chain)
            {
                if (Identity.IsLink(d) || File.Exists(d)) throw Incomplete("the asset-creation record area is a link or a file; nothing was changed");
                if (!Directory.Exists(d))
                {
                    if (!create) return null;
                    Directory.CreateDirectory(d);
                    if (Identity.IsLink(d)) throw Incomplete("the asset-creation record area is a link; nothing was changed");
                }
            }
            return chain[3];
        }

        public static List<CreateTxn> Load()
        {
            string dir = Dir(false);
            var records = new List<CreateTxn>();
            if (dir == null) return records;
            foreach (var entry in Directory.GetFileSystemEntries(dir).OrderBy(e => e, StringComparer.Ordinal))
            {
                string name = Path.GetFileName(entry);
                if (name.StartsWith(".tmp-", StringComparison.Ordinal)) continue;   // an unrenamed record write: never authoritative
                var m = RecordName.Match(name);
                if (!m.Success || Identity.IsLink(entry) || !File.Exists(entry)) throw Incomplete("the asset-creation record area holds an unknown entry (" + name + ")");
                if (new FileInfo(entry).Length > AssetBounds.MaxRecordBytes) throw Incomplete("an asset-creation record is larger than its bound");
                string text;
                try { text = new UTF8Encoding(false, true).GetString(File.ReadAllBytes(entry)); }
                catch (ArgumentException) { throw Incomplete("an asset-creation record is not UTF-8"); }
                records.Add(CreateTxn.Parse(text, m.Groups[1].Value, LiveBridge.Place.Key));
                if (records.Count > AssetBounds.MaxRecords) throw Incomplete("more than " + AssetBounds.MaxRecords + " asset-creation records are open");
            }
            return records.OrderBy(t => t.StartedUtc, StringComparer.Ordinal).ThenBy(t => t.TxnId, StringComparer.Ordinal).ToList();
        }

        public static void Write(CreateTxn t)
        {
            string text = t.Write();
            if (Encoding.UTF8.GetByteCount(text) > AssetBounds.MaxRecordBytes) throw new Refusal("ASSET_LIMIT", "the asset-creation record exceeds its bound");
            Ipc.AtomicReplace(Path.Combine(Dir(true), t.TxnId + ".json"), text);
        }

        public static void Delete(CreateTxn t)
        {
            string dir = Dir(false);
            if (dir == null) return;
            string path = Path.Combine(dir, t.TxnId + ".json");
            if (File.Exists(path) || Identity.IsLink(path)) File.Delete(path);
        }

        // ------------------------------------------------------------ what is on disk

        internal sealed class State
        {
            public string Scratch, ScratchMeta, Temp, Final;
            public Dictionary<string, object> ToData()
            {
                return new Dictionary<string, object> { { "scratch", Scratch }, { "scratch_meta", ScratchMeta }, { "temp", Temp }, { "final", Final } };
            }
        }

        static bool Exact(CreateTxn t, string path, string file, string meta)
        {
            if (t.PhaseIndex < 2) return false;
            try
            {
                if (AssetFiles.FileSha(path) != file || AssetFiles.MetaSha(path) != meta || AssetFiles.MetaGuid(path) != t.Guid) return false;
                if (AssetDatabase.AssetPathToGUID(path, AssetPathToGUIDOptions.OnlyExistingAssets) != t.Guid) return false;
                var o = AssetDatabase.LoadMainAssetAtPath(path);
                return o != null && SceneObjects.Id(o) == t.GlobalId && Catalog.TypeKey(o.GetType()) == t.Type;
            }
            catch (Refusal) { return false; }
        }

        //   scratch      ABSENT | EMPTY | HOLDS_TEMP (exactly the temporary file and its .meta) | UNKNOWN
        //   scratch_meta ABSENT | EXACT | DIFFERENT
        //   temp         ABSENT | EXACT | DIFFERENT (present but not proven identical)
        //   final        ABSENT | EXACT | OURS_DIFFERENT (this transaction's GUID, changed) | OTHER
        public static State Facts(CreateTxn t)
        {
            var s = new State();
            string scratch = t.ScratchFolder, temp = t.TempPath, final = t.FinalPath;
            string file = temp.Substring(scratch.Length + 1);
            var listing = Identity.IsLink(AssetFiles.Full(scratch)) || File.Exists(AssetFiles.Full(scratch)) ? null : AssetFiles.Listing(scratch);
            if (!AssetFiles.Present(scratch)) s.Scratch = "ABSENT";
            else if (listing == null) s.Scratch = "UNKNOWN";
            else if (listing.Count == 0) s.Scratch = "EMPTY";
            else if (listing.SequenceEqual(new[] { file, file + ".meta" })) s.Scratch = "HOLDS_TEMP";
            else s.Scratch = "UNKNOWN";
            string scratchMeta;
            try { scratchMeta = AssetFiles.MetaSha(scratch); }
            catch (Refusal) { scratchMeta = "?"; }
            s.ScratchMeta = scratchMeta == null ? "ABSENT" : t.ScratchMeta != null && scratchMeta == t.ScratchMeta ? "EXACT" : "DIFFERENT";
            if (!AssetFiles.Present(temp) && !AssetFiles.Present(temp + ".meta")) s.Temp = "ABSENT";
            else s.Temp = Exact(t, temp, t.TempFile, t.TempMeta) ? "EXACT" : "DIFFERENT";
            string folder = final.Substring(0, final.LastIndexOf('/')), name = final.Substring(folder.Length + 1);
            bool present = AssetFiles.Present(final) || AssetFiles.Present(final + ".meta") || AssetFiles.CaseCollision(folder, name) ||
                           AssetFiles.CaseCollision(folder, name + ".meta") || !string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(final, AssetPathToGUIDOptions.OnlyExistingAssets));
            if (!present) s.Final = "ABSENT";
            else if (Exact(t, final, t.FinalFile ?? t.TempFile, t.FinalMeta ?? t.TempMeta)) s.Final = "EXACT";
            else s.Final = t.Guid != null && AssetFiles.MetaGuid(final) == t.Guid ? "OURS_DIFFERENT" : "OTHER";
            return s;
        }

        static bool ScratchOwned(State s)
        {
            return (s.Scratch == "ABSENT" && s.ScratchMeta == "ABSENT") ||
                   ((s.Scratch == "EMPTY" || s.Scratch == "HOLDS_TEMP") && s.ScratchMeta == "EXACT");
        }

        // Removes the scratch folder when it is exactly the empty folder of this transaction; returns why not, or null.
        public static string RemoveScratch(CreateTxn t)
        {
            var s = Facts(t);
            if (s.Scratch == "ABSENT" && s.ScratchMeta == "ABSENT") return null;
            if (s.Scratch != "EMPTY") return "holds content GPOS did not create";
            if (s.ScratchMeta != "EXACT") return "has a .meta file GPOS did not create";
            if (!AssetDatabase.DeleteAsset(t.ScratchFolder) || AssetFiles.Present(t.ScratchFolder) || AssetFiles.Present(t.ScratchFolder + ".meta"))
                return "could not be removed";
            return null;
        }

        // Inside a failed request (never after a move was attempted): removes this transaction's own exact temporary
        // asset and exact scratch folder and clears the record. Returns what was done, or null when nothing provable.
        public static string RemoveOwnTemp(CreateTxn t, State s)
        {
            if (!ScratchOwned(s)) return null;
            string done;
            if (s.Temp == "EXACT")
            {
                if (!AssetDatabase.DeleteAsset(t.TempPath) || AssetFiles.Present(t.TempPath) || AssetFiles.Present(t.TempPath + ".meta")) return null;
                done = "TEMP_REMOVED";
            }
            else if (s.Temp == "ABSENT" && s.Scratch != "HOLDS_TEMP") done = "NOTHING_CREATED";
            else return null;
            if (RemoveScratch(t) != null) return null;
            Delete(t);
            return done;
        }

        // Recovers every earlier interrupted creation of this project from what is on disk. Returns what was done;
        // throws CREATE_INCOMPLETE (with bounded facts) at the first record that cannot be decided from proof alone.
        public static List<object> RecoverAll()
        {
            var done = new List<object>();
            List<CreateTxn> records;
            try { records = Load(); }
            catch (Refusal refusal) { throw Incomplete(refusal.Message, new Dictionary<string, object> { { "mutation_started", false } }); }
            foreach (var t in records)
            {
                var s = Facts(t);
                string outcome = Decide(t, s);
                if (outcome == null)
                    throw Incomplete("an earlier asset creation (" + t.TxnId + ", " + t.Phase + ", " + t.FinalPath + ") cannot be finished or undone from proven facts; " +
                                     "nothing was deleted, moved or overwritten — resolve it outside GPOS (the record names what to inspect)",
                                     new Dictionary<string, object> { { "mutation_started", done.Any(x => (bool)((Dictionary<string, object>)x)["changed"]) },
                                                                      { "transaction", new Dictionary<string, object> { { "txn_id", t.TxnId }, { "phase", t.Phase },
                                                                          { "kind", t.Kind }, { "final_path", t.FinalPath }, { "session_id", t.SessionId },
                                                                          { "request_id", t.RequestId }, { "owner", t.Owner } } },
                                                                      { "state", s.ToData() }, { "recovered", done } },
                                     t.Kind == AssetKinds.Prefab ? "PREFAB_CREATE_INCOMPLETE" : "CREATE_INCOMPLETE");
                done.Add(new Dictionary<string, object> { { "txn_id", t.TxnId }, { "kind", t.Kind }, { "final_path", t.FinalPath }, { "outcome", outcome },
                                                           { "changed", outcome == "TEMP_REMOVED" || s.Scratch != "ABSENT" } });
            }
            return done;
        }

        // NOTHING_CREATED, TEMP_REMOVED, FINAL_KEPT, ABANDONED — performed — or null (undecidable: nothing touched).
        static string Decide(CreateTxn t, State s)
        {
            if (t.PhaseIndex < 2)
            {
                bool nothing = s.Temp == "ABSENT" && s.Scratch != "HOLDS_TEMP" &&
                               ((s.Scratch == "ABSENT" && s.ScratchMeta == "ABSENT") || (t.Phase == CreateTxn.ScratchReady && s.Scratch == "EMPTY" && s.ScratchMeta == "EXACT"));
                if (!nothing || RemoveScratch(t) != null) return null;
                Delete(t);
                return "NOTHING_CREATED";
            }
            if (!ScratchOwned(s)) return null;
            if (s.Temp == "EXACT" && s.Final == "ABSENT")
            {
                if (!AssetDatabase.DeleteAsset(t.TempPath) || AssetFiles.Present(t.TempPath) || AssetFiles.Present(t.TempPath + ".meta")) return null;
                if (RemoveScratch(t) != null) return null;
                Delete(t);
                return "TEMP_REMOVED";
            }
            if (s.Temp == "ABSENT" && s.Scratch != "HOLDS_TEMP" && (s.Final == "EXACT" || s.Final == "ABSENT" || s.Final == "OTHER"))
            {
                if (RemoveScratch(t) != null) return null;
                Delete(t);
                return s.Final == "EXACT" ? "FINAL_KEPT" : "ABANDONED";
            }
            return null;
        }
    }
}
