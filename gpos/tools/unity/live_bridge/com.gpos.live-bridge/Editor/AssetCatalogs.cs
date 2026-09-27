// GPOS live bridge — the closed asset catalogs (bridge 1.2.0), rebuilt from the Editor's own type and shader
// information on every use, so they follow every Domain Reload and import. Nothing is looked up outside them.
//
// ScriptableObject catalog: a concrete, non-generic, public, non-obsolete ScriptableObject type from a Player
// (non-Editor, non-test) assembly of the project that is the class of exactly one runtime MonoScript and not a
// StateMachineBehaviour. Every entry may be referenced and edited; only entries with [CreateAssetMenu] are creatable.
// Shader catalog: every supported shader without errors that is not Hidden/, has an asset identity (identifier type
// 1, 3 or 4) and declares at most MaxShaderProperties properties; each property with its type, flags, range and
// texture dimension. Both digests (AssetCatalogDigest) cover every listed field.
using System;
using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEditor.Compilation;
using UnityEngine;
using UnityEngine.Rendering;

namespace Gpos.LiveBridge
{
    internal sealed class ScriptableCatalog
    {
        public List<ScriptableEntry> Entries = new List<ScriptableEntry>();
        public Dictionary<string, Type> Types = new Dictionary<string, Type>(StringComparer.Ordinal);
        public string Digest;

        public ScriptableEntry Find(string typeId) { return Entries.FirstOrDefault(e => e.TypeId == typeId); }
    }

    internal sealed class ShaderCatalog
    {
        public List<ShaderEntry> Entries = new List<ShaderEntry>();
        public Dictionary<string, Shader> Shaders = new Dictionary<string, Shader>(StringComparer.Ordinal);
        public string Digest;

        public ShaderEntry Find(string id) { return Entries.FirstOrDefault(e => e.Id == id); }

        public ShaderEntry Of(Shader s) { return s == null ? null : Find(SceneObjects.Id(s)); }
    }

    internal static class AssetCatalogs
    {
        static ScriptableCatalog scriptables;

        // Forgets the catalog built for the previous request: every request sees the catalog as it is now.
        public static void Reset() { scriptables = null; }

        // Built at most once per request (Reset runs before every authoring command).
        public static ScriptableCatalog ScriptableObjects()
        {
            if (scriptables != null) return scriptables;
            var player = new HashSet<string>(CompilationPipeline.GetAssemblies(AssembliesType.PlayerWithoutTestAssemblies).Select(a => a.name), StringComparer.Ordinal);
            var scripts = new Dictionary<Type, int>();
            foreach (var m in MonoImporter.GetAllRuntimeMonoScripts())
            {
                if (m == null) continue;
                var k = m.GetClass();
                if (k == null) continue;
                int n;
                scripts[k] = scripts.TryGetValue(k, out n) ? n + 1 : 1;
            }
            var catalog = new ScriptableCatalog();
            foreach (var t in TypeCache.GetTypesDerivedFrom<ScriptableObject>())
            {
                if (t.IsAbstract || t.IsGenericTypeDefinition || t.ContainsGenericParameters || !t.IsVisible) continue;
                if (t.IsDefined(typeof(ObsoleteAttribute), false)) continue;
                if (typeof(StateMachineBehaviour).IsAssignableFrom(t)) continue;
                string assembly = t.Assembly.GetName().Name;
                if (!player.Contains(assembly)) continue;
                int mapped;
                if (!scripts.TryGetValue(t, out mapped) || mapped != 1) continue;
                var menu = t.GetCustomAttributes(typeof(CreateAssetMenuAttribute), false).OfType<CreateAssetMenuAttribute>().FirstOrDefault();
                var e = new ScriptableEntry {
                    TypeId = Catalog.TypeKey(t), Assembly = assembly, FullName = t.FullName, Name = t.Name, Namespace = t.Namespace ?? "",
                    ScriptMapped = true, Creatable = menu != null, MenuName = menu == null ? "" : menu.menuName ?? "",
                    FileName = menu == null ? "" : menu.fileName ?? "" };
                if (catalog.Types.ContainsKey(e.TypeId)) continue;
                catalog.Types[e.TypeId] = t;
                catalog.Entries.Add(e);
            }
            catalog.Entries = catalog.Entries.OrderBy(e => e.TypeId, StringComparer.Ordinal).ToList();
            catalog.Digest = AssetCatalogDigest.ScriptableObjects(catalog.Entries);
            scriptables = catalog;
            return catalog;
        }

        public static ShaderCatalog Shaders()
        {
            var catalog = new ShaderCatalog();
            foreach (var info in ShaderUtil.GetAllShaderInfo())
            {
                if (!info.supported || info.hasErrors || info.name.StartsWith("Hidden/", StringComparison.Ordinal)) continue;
                var shader = Shader.Find(info.name);
                if (shader == null || !shader.isSupported) continue;
                string id = SceneObjects.Id(shader);
                if (!AssetIds.IsAsset(id) || catalog.Shaders.ContainsKey(id)) continue;
                try { AssetIds.Check(id); }
                catch (Refusal) { continue; }
                int count = shader.GetPropertyCount();
                if (count > AssetBounds.MaxShaderProperties) continue;
                var entry = new ShaderEntry { Id = id, Name = shader.name };
                for (int i = 0; i < count; i++)
                {
                    var type = shader.GetPropertyType(i);
                    var flags = shader.GetPropertyFlags(i);
                    var p = new ShaderProperty {
                        Name = shader.GetPropertyName(i), Type = type.ToString(),
                        HideInInspector = (flags & ShaderPropertyFlags.HideInInspector) != 0,
                        PerRendererData = (flags & ShaderPropertyFlags.PerRendererData) != 0,
                        NonModifiableTexture = (flags & ShaderPropertyFlags.NonModifiableTextureData) != 0 };
                    if (type == ShaderPropertyType.Range)
                    {
                        var limits = shader.GetPropertyRangeLimits(i);
                        p.RangeMin = limits.x;
                        p.RangeMax = limits.y;
                    }
                    if (type == ShaderPropertyType.Texture) p.Dimension = shader.GetPropertyTextureDimension(i).ToString();
                    entry.Properties.Add(p);
                }
                catalog.Entries.Add(entry);
                catalog.Shaders[id] = shader;
                if (catalog.Entries.Count > AssetBounds.MaxShaders)
                    throw new Refusal("ASSET_LIMIT", "the project has more than " + AssetBounds.MaxShaders + " catalogable shaders; the catalog is never truncated");
            }
            catalog.Entries = catalog.Entries.OrderBy(e => e.Id, StringComparer.Ordinal).ToList();
            catalog.Digest = AssetCatalogDigest.Shaders(catalog.Entries);
            return catalog;
        }
    }
}
